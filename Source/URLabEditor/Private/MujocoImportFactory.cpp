// Copyright (c) 2026 Jonathan Embley-Riches. All rights reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//
// --- LEGAL DISCLAIMER ---
// UnrealRoboticsLab is an independent software plugin. It is NOT affiliated with,
// endorsed by, or sponsored by Epic Games, Inc. "Unreal" and "Unreal Engine" are
// trademarks or registered trademarks of Epic Games, Inc. in the US and elsewhere.
//
// This plugin incorporates third-party software: MuJoCo (Apache 2.0),
// CoACD (MIT), and libzmq (MPL 2.0). See ThirdPartyNotices.txt for details.

#include "MujocoImportFactory.h"
#include "MujocoGenerationAction.h"
#include "MjPythonHelper.h"
#include "MuJoCo/Core/MjArticulation.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Engine/Blueprint.h"
#include "Misc/FeedbackContext.h"
#include "Misc/Paths.h"
#include "Misc/ScopedSlowTask.h"
#include "Interfaces/IPluginManager.h"
#include "RenderingThread.h"
#include "ShaderCompiler.h"
#include "URLabEditorLogging.h"
#include "XmlFile.h"
#include "Misc/MessageDialog.h"
#include "Misc/App.h"

// ============================================================================
// Angle-units sanity gate
// ============================================================================
// MJCF defaults to angle="degree" when no <compiler> tag is present, but
// menagerie/Playground-style robot files author joint ranges in RADIANS and
// rely on <compiler angle="radian"> — a tag that is easily lost when the file
// is copied around. Importing such a file silently compiles every joint limit
// /57.3 (e.g. a Go1 thigh range of [-0.686, 4.501] rad becomes ~±1°): the legs
// are pinned nearly straight, actuators saturate against the limits, and the
// robot is uncontrollable.
//
// Heuristic: if the file declares NO explicit angle units anywhere, yet every
// hinge/ball <joint range> value is within ±2π, the ranges are almost
// certainly radians — a real robot whose joint limits all sit inside ±6.3
// DEGREES does not exist. Warn (and let the user abort) before any values are
// baked into components.
//
// Returns false when the user chose to abort the import.
static bool URLabCheckMjcfAngleUnits(const FString& Filename)
{
	FXmlFile Xml(Filename);
	if (!Xml.IsValid())
	{
		return true; // XML errors surface later in the normal import path
	}
	const FXmlNode* Root = Xml.GetRootNode();
	if (!Root || !Root->GetTag().Equals(TEXT("mujoco"), ESearchCase::IgnoreCase))
	{
		return true; // not a MuJoCo model file
	}

	bool bExplicitAngle = false;
	double MaxAbsRange = 0.0;
	int32 NumRangeValues = 0;

	TFunction<void(const FXmlNode*)> Walk = [&](const FXmlNode* Node)
	{
		for (const FXmlNode* Child : Node->GetChildrenNodes())
		{
			const FString Tag = Child->GetTag();
			if (Tag.Equals(TEXT("compiler"), ESearchCase::IgnoreCase))
			{
				if (!Child->GetAttribute(TEXT("angle")).IsEmpty())
				{
					bExplicitAngle = true;
				}
			}
			else if (Tag.Equals(TEXT("joint"), ESearchCase::IgnoreCase))
			{
				// Only angular joints: hinge (default when type is omitted) and
				// ball ranges are angles; slide ranges are metres — skip those.
				const FString Type = Child->GetAttribute(TEXT("type"));
				const bool bAngular = Type.IsEmpty() ||
					Type.Equals(TEXT("hinge"), ESearchCase::IgnoreCase) ||
					Type.Equals(TEXT("ball"), ESearchCase::IgnoreCase);
				const FString Range = Child->GetAttribute(TEXT("range"));
				if (bAngular && !Range.IsEmpty())
				{
					TArray<FString> Parts;
					Range.ParseIntoArrayWS(Parts);
					for (const FString& P : Parts)
					{
						MaxAbsRange = FMath::Max(MaxAbsRange, FMath::Abs(FCString::Atod(*P)));
						++NumRangeValues;
					}
				}
			}
			Walk(Child);
		}
	};
	Walk(Root);

	if (bExplicitAngle || NumRangeValues == 0 || MaxAbsRange > 6.6)
	{
		return true; // explicit units, no ranged angular joints, or clearly degrees
	}

	UE_LOG(LogURLabEditor, Warning,
		TEXT("MujocoImportFactory: '%s' declares no <compiler angle=...> (MJCF default is "
			 "DEGREES) but all %d hinge/ball range values are within ±2π (max |v| = %.3f) — "
			 "they look like RADIANS. Importing as-is shrinks every joint limit ~57.3x and "
			 "the robot will be locked nearly rigid. Add <compiler angle=\"radian\" "
			 "autolimits=\"true\"/> to the file and re-import."),
		*Filename, NumRangeValues, MaxAbsRange);

	if (FApp::IsUnattended() || GIsRunningUnattendedScript)
	{
		return true; // headless import: warn in the log but do not block
	}

	const FText Msg = FText::Format(NSLOCTEXT("URLab", "MjcfAngleUnitsSuspicion",
		"'{0}' has no <compiler angle=...> tag, so MuJoCo will read its angles as DEGREES.\n\n"
		"However, every hinge/ball joint range in the file is within ±2π "
		"(max |value| = {1}) — these look like RADIANS. Imported as degrees, all joint "
		"limits shrink ~57x and the robot will be locked nearly rigid.\n\n"
		"Recommended: cancel, add <compiler angle=\"radian\" autolimits=\"true\"/> to the "
		"file, then import again.\n\nContinue importing anyway?"),
		FText::FromString(FPaths::GetCleanFilename(Filename)),
		FText::AsNumber(MaxAbsRange));

	return FMessageDialog::Open(EAppMsgType::YesNo, Msg) == EAppReturnType::Yes;
}

UMujocoImportFactory::UMujocoImportFactory()
{
	Formats.Add(TEXT("xml;MuJoCo XML File"));
	SupportedClass = UBlueprint::StaticClass();
	bCreateNew = false;
	bEditorImport = true;
}

bool UMujocoImportFactory::FactoryCanImport(const FString& Filename)
{
	return FPaths::GetExtension(Filename).Equals(TEXT("xml"), ESearchCase::IgnoreCase);
}

UObject* UMujocoImportFactory::FactoryCreateFile(UClass* InClass, UObject* InParent, FName InName, EObjectFlags Flags, const FString& Filename, const TCHAR* Parms, FFeedbackContext* Warn, bool& bOutOperationCanceled)
{
	// Gate: catch radian-authored files about to be read as degrees BEFORE any
	// values are baked into components (see URLabCheckMjcfAngleUnits above).
	if (!URLabCheckMjcfAngleUnits(Filename))
	{
		UE_LOG(LogURLabEditor, Log, TEXT("Import of '%s' cancelled by user (angle-units check)."), *Filename);
		bOutOperationCanceled = true;
		return nullptr;
	}

	// Create blueprint based on AMjArticulation
	UClass* ParentClass = AMjArticulation::StaticClass();

	// Defensive: FKismetEditorUtilities::CreateBlueprint asserts when
	// an existing UBlueprint with the same name is still in memory
	// (Kismet2.cpp:424 -- FindObject<UBlueprint>(Outer, Name) == 0).
	// Callers that want to overwrite should drive ImportXmlSync with
	// force_reimport=true, which destroys the existing asset first; if
	// we get here with a stale BP anyway, bail with a logged error
	// rather than crashing the editor.
	if (UBlueprint* Existing = FindObject<UBlueprint>(InParent, *InName.ToString()))
	{
		UE_LOG(LogURLabEditor, Error,
			TEXT("MujocoImportFactory: refusing to overwrite existing Blueprint '%s' in '%s'. "
				 "Call ImportXmlSync with force_reimport=true (which destroys the existing asset) "
				 "before importing again."),
			*InName.ToString(),
			InParent ? *InParent->GetPathName() : TEXT("<no outer>"));
		bOutOperationCanceled = true;
		return nullptr;
	}

	// Create the Blueprint Asset
	UBlueprint* NewBP = FKismetEditorUtilities::CreateBlueprint(
		ParentClass,
		InParent,
		InName,
		BPTYPE_Normal,
		UBlueprint::StaticClass(),
		UBlueprintGeneratedClass::StaticClass());

	if (NewBP)
	{
		FScopedSlowTask SlowTask(4.f, NSLOCTEXT("URLab", "ImportingMuJoCo", "Importing MuJoCo model..."));
		SlowTask.MakeDialog(/*bShowCancelButton=*/false);

		// Step 0: Try to run clean_meshes_trimesh.py to prepare meshes
		SlowTask.EnterProgressFrame(1.f, NSLOCTEXT("URLab", "ImportStep0", "Preparing meshes..."));

		FString ActualXmlPath = Filename;
		{
			FString PluginDir = IPluginManager::Get().FindPlugin("UnrealRoboticsLab")->GetBaseDir();
			FString ScriptPath = FPaths::Combine(PluginDir, TEXT("Scripts/clean_meshes.py"));

			if (FPaths::FileExists(ScriptPath))
			{
				bool bCancelled = false;
				FString PythonExe = FMjPythonHelper::EnsurePythonReady(bCancelled);

				if (bCancelled)
				{
					UE_LOG(LogURLabEditor, Log, TEXT("Import cancelled by user during Python setup."));
					return nullptr;
				}

				if (!PythonExe.IsEmpty())
				{
					// Run the clean script
					int32 ReturnCode = -1;
					FString StdOut, StdErr;
					FString Args = FString::Printf(TEXT("\"%s\" \"%s\""), *ScriptPath, *Filename);
					UE_LOG(LogURLabEditor, Log, TEXT("Running mesh preparation: %s %s"), *PythonExe, *Args);
					FPlatformProcess::ExecProcess(*PythonExe, *Args, &ReturnCode, &StdOut, &StdErr);

					if (ReturnCode == 0)
					{
						FString UeXmlPath = FPaths::Combine(
							FPaths::GetPath(Filename),
							FPaths::GetBaseFilename(Filename) + TEXT("_ue.xml"));

						if (FPaths::FileExists(UeXmlPath))
						{
							UE_LOG(LogURLabEditor, Log, TEXT("Using prepared XML: %s"), *UeXmlPath);
							ActualXmlPath = UeXmlPath;
						}
					}
					else
					{
						UE_LOG(LogURLabEditor, Warning, TEXT("Mesh preparation script failed (code %d). Using original XML."), ReturnCode);
						if (!StdErr.IsEmpty())
							UE_LOG(LogURLabEditor, Warning, TEXT("  stderr: %s"), *StdErr);
					}
				}
				else
				{
					UE_LOG(LogURLabEditor, Log, TEXT("Python not configured — skipping mesh preparation."));
				}
			}
		}

		SlowTask.EnterProgressFrame(1.f, NSLOCTEXT("URLab", "ImportStep1", "Reading XML..."));

		// Set XML Path in CDO so it persists (use original path, not _ue variant)
		AMjArticulation* CDO = Cast<AMjArticulation>(NewBP->GeneratedClass->GetDefaultObject());
		if (CDO)
		{
			CDO->MuJoCoXMLFile.FilePath = Filename;
			CDO->MarkPackageDirty();
		}

		SlowTask.EnterProgressFrame(1.f, NSLOCTEXT("URLab", "ImportStep2", "Building Blueprint components..."));

		// Generate Components using the (potentially prepared) XML
		UMujocoGenerationAction* Generator = NewObject<UMujocoGenerationAction>();
		Generator->GenerateForBlueprint(NewBP, ActualXmlPath);

		SlowTask.EnterProgressFrame(1.f, NSLOCTEXT("URLab", "ImportStep3", "Compiling Blueprint..."));

		// Compile to save changes and ensure components are valid
		FKismetEditorUtilities::CompileBlueprint(NewBP);

		// Wait for all shaders to finish compiling and flush render commands.
		// Material instances created during import trigger async shader compilation.
		// If the content browser renders thumbnails before shaders are ready,
		// the render thread crashes (UE-23902).
		if (GShaderCompilingManager)
		{
			GShaderCompilingManager->FinishAllCompilation();
		}
		FlushRenderingCommands();
	}

	bOutOperationCanceled = false;
	return NewBP;
}
