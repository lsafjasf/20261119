// Player / recovery state machine.
//
// IDLE -> PLAYING -> (stall detected / load error) -> RECOVERING
// RECOVERING -> FALLING_BACK (when bitrate is stepped down) -> PLAYING
// RECOVERING -> TERMINATED only after the consecutive-failure cap is hit.
export const PlayerState = Object.freeze({
  IDLE: "IDLE",
  PLAYING: "PLAYING",
  RECOVERING: "RECOVERING",
  FALLING_BACK: "FALLING_BACK",
  TERMINATED: "TERMINATED",
});

export const RecoveryAction = Object.freeze({
  RETRY: "retry",
  FALLBACK_BITRATE: "fallback_bitrate",
  CATCH_UP: "catch_up",
  TERMINATE: "terminate",
});
