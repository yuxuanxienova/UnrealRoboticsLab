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

#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "MjInputHandler.generated.h"

// Forward declarations
class AAMjManager;
class FMjMouseDeltaProcessor;

/**
 * @class UMjInputHandler
 * @brief Handles keyboard hotkeys for the MuJoCo simulation (debug toggles, pause, reset, etc.).
 */
UCLASS(ClassGroup = (MuJoCo), meta = (BlueprintSpawnableComponent))
class URLAB_API UMjInputHandler : public UActorComponent
{
	GENERATED_BODY()

public:
	UMjInputHandler();

	virtual void BeginPlay() override;
	virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;
	virtual void TickComponent(float DeltaTime, ELevelTick TickType, FActorComponentTickFunction* ThisTickFunction) override;

private:
	/** @brief Processes keyboard hotkeys for simulation control and debug toggles. */
	void ProcessHotkeys(APlayerController* PC);

	/** @brief Drives perturbation gestures: LMB press selects a MuJoCo body,
	 *         LMB drag translates the perturb target, and release stops drag. */
	void ProcessPerturbation(APlayerController* PC, float DeltaTime);

	/** @brief Previous-frame plain LMB held state for press/release edge detection. */
	bool bPrevPlainLmbHeld = false;

	/** @brief Whether we currently hold a camera-input lock on the player
	 *         controller. SetIgnoreLookInput/MoveInput are reference-counted,
	 *         so we must balance each push() with exactly one pop(). */
	bool bPerturbationCameraLocked = false;

	/** @brief Screen-space position of the cursor at LMB press, plus the
	 *         running sum of raw pixel deltas since. We rebuild a "virtual"
	 *         cursor ray from these each tick because UE's mouse capture freezes
	 *         GetMousePosition/DeprojectMousePositionToWorld while held —
	 *         identical reason we needed the Slate pre-processor. */
	FVector2D TranslateClickScreen = FVector2D::ZeroVector;
	FVector2D TranslateAccumPixels = FVector2D::ZeroVector;

	/** Pushes camera lock if not already held. */
	void LockCamera(class APlayerController* PC);
	/** Releases camera lock if held. */
	void UnlockCamera(class APlayerController* PC);

	/** @brief Slate input pre-processor that captures raw mouse deltas from
	 *         FPointerEvent before UE's cursor-capture layer zeroes them out.
	 *         Without this, LMB capture locks GetMousePosition / GetCursorPos
	 *         and any derived delta is zero, breaking rotate. */
	TSharedPtr<FMjMouseDeltaProcessor> MouseDeltaProcessor;
};
