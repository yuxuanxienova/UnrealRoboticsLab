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

#pragma once

#include "CoreMinimal.h"

// Per-frame metadata header prepended to streamed camera pixels on BOTH the
// ZMQ and SHM transports. The wire layout must match urlab_client's
// CAMERA_META_STRUCT_V2 (struct.Struct("<IIQdIId"), 40 bytes, little-endian):
//   v1 (32B): magic(u32) version(u32) frame_id(u64) sim_time(f64) width(u32) height(u32)
//   v2 (40B): ... + capture_unix_time(f64)
// Consumers that predate the header fall back gracefully: parse_camera_frame
// checks the magic and returns the whole payload as pixels on mismatch.
#pragma pack(push, 1)
struct FMjCameraFrameMeta
{
	/** 'UCM1' little-endian. */
	uint32 Magic = 0x314D4355;

	/** Header version; 2 adds CaptureUnixTime. */
	uint32 Version = 2;

	/** Monotonic render-snapshot FrameId the frame's camera pose belongs
	 *  to (FMjRenderSnapshot::FrameId at readback request). */
	uint64 FrameId = 0;

	/** MuJoCo sim time (s) of the render state the frame shows. */
	double SimTime = 0.0;

	uint32 Width = 0;
	uint32 Height = 0;

	/** Unix seconds (UTC wall clock) at readback request. Lets a consumer
	 *  pair the frame with the base pose at capture instead of "latest". */
	double CaptureUnixTime = 0.0;
};
#pragma pack(pop)

static_assert(sizeof(FMjCameraFrameMeta) == 40,
	"FMjCameraFrameMeta must stay wire-compatible with urlab_client CAMERA_META_STRUCT_V2 (40 bytes)");
