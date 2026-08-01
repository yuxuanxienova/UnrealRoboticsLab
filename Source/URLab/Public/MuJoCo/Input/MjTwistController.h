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
#include "InputActionValue.h"
#include "MjTwistController.generated.h"

class UInputAction;
class UInputMappingContext;
class UEnhancedInputComponent;

/**
 * Captures WASD/gamepad and external twist commands (vx, vy, yaw_rate).
 * Local UI input and external ZMQ/RPC input are stored in separate slots so
 * the active EControlSource can select exactly one writer for cmd_vel.
 *
 * Thread-safe: game/RPC threads write twist state via input callbacks or RPC,
 * physics thread reads via GetTwistForSource().
 */
UCLASS(ClassGroup = (Custom), meta = (BlueprintSpawnableComponent))
class URLAB_API UMjTwistController : public UActorComponent
{
	GENERATED_BODY()

public:
	UMjTwistController();

	// ─── Input Assets ───

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "MuJoCo|Input")
	UInputMappingContext* TwistMappingContext = nullptr;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "MuJoCo|Input")
	UInputAction* MoveAction = nullptr;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "MuJoCo|Input")
	UInputAction* TurnAction = nullptr;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "MuJoCo|Input")
	TArray<UInputAction*> ActionKeys;

	// ─── Velocity Limits ───

	/** Maximum forward/backward speed (m/s). Full stick or W/S produces this velocity. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "MuJoCo|Twist")
	float MaxVx = 0.8f;

	/** Maximum strafe speed (m/s). Full stick or A/D produces this velocity. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "MuJoCo|Twist")
	float MaxVy = 0.5f;

	/** Maximum yaw rate (rad/s). Full stick or Q/E produces this rate. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "MuJoCo|Twist")
	float MaxYawRate = 1.57f;

	// ─── Thread-Safe Accessors ───

	/** Returns the external/ZMQ twist as FVector(Vx, Vy, YawRate). Thread-safe. */
	FVector GetTwist() const;

	/** Returns twist for a specific control source. Source: 0 = ZMQ, 1 = UI. Thread-safe. */
	FVector GetTwistForSource(uint8 Source) const;

	/** Returns bitmask of currently pressed action keys (bits 0-9). Thread-safe. */
	int32 GetActiveActions() const;

	/** Bind input actions to an EnhancedInputComponent. Called from AMjArticulation::SetupPlayerInputComponent. */
	void BindInput(UEnhancedInputComponent* EIC);

	/** Zero all twist state. Called when unpossessed. */
	void ResetTwist();

	/** Inject twist directly (m/s, m/s, rad/s). Bypasses input mapping; used by
	 *  the bridge's `set_twist` RPC for headless / Python-driven runs. Thread-safe. */
	void SetTwist(float InVx, float InVy, float InYawRate);

	/** Inject local UE/UI twist. Used by WASD/gamepad and the on-screen joystick. Thread-safe. */
	void SetUITwist(float InVx, float InVy, float InYawRate);

private:
	void SetTwistForSource(uint8 Source, float InVx, float InVy, float InYawRate);

	// Input handlers
	void OnMove(const FInputActionValue& Value);
	void OnMoveCompleted(const FInputActionValue& Value);
	void OnTurn(const FInputActionValue& Value);
	void OnTurnCompleted(const FInputActionValue& Value);
	void OnActionPressed(const FInputActionValue& Value, int32 Index);
	void OnActionReleased(const FInputActionValue& Value, int32 Index);

	mutable FCriticalSection TwistMutex;
	float ZmqVx = 0.f;
	float ZmqVy = 0.f;
	float ZmqYawRate = 0.f;
	float UiVx = 0.f;
	float UiVy = 0.f;
	float UiYawRate = 0.f;
	int32 ActionBitmask = 0;
};
