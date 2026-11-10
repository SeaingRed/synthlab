// A test-only build of vmath.ts's kernels (tests/studio/test_voice_wasm.py
// compiles this when node_modules is present and checks them against
// Math.sin / Math.tanh). Not shipped.
import { vexp, vexp2, vsin, vtanh } from "./vmath";

export function expL(a: f64, b: f64, lane: i32): f64 {
  const v = vexp(f64x2.replace_lane(f64x2.splat(a), 1, b));
  return lane ? f64x2.extract_lane(v, 1) : f64x2.extract_lane(v, 0);
}

export function exp2L(a: f64, b: f64, lane: i32): f64 {
  const v = vexp2(f64x2.replace_lane(f64x2.splat(a), 1, b));
  return lane ? f64x2.extract_lane(v, 1) : f64x2.extract_lane(v, 0);
}

export function sin2(a: f64, b: f64, lane: i32): f64 {
  const v = vsin(f64x2.replace_lane(f64x2.splat(a), 1, b));
  return lane ? f64x2.extract_lane(v, 1) : f64x2.extract_lane(v, 0);
}

export function tanh2(a: f64, b: f64, lane: i32): f64 {
  const v = vtanh(f64x2.replace_lane(f64x2.splat(a), 1, b));
  return lane ? f64x2.extract_lane(v, 1) : f64x2.extract_lane(v, 0);
}
