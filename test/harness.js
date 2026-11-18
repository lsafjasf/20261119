// Minimal zero-dependency test harness for Node ESM.

let passed = 0;
let failed = 0;
const failures = [];

export function describe(name, fn) {
  console.log(`\n${name}`);
  fn();
}

export function it(name, fn) {
  try {
    fn();
    passed++;
    console.log(`  \u2713 ${name}`);
  } catch (err) {
    failed++;
    failures.push({ name, err });
    console.log(`  \u2717 ${name}`);
    console.log(`      ${err.message}`);
  }
}

export function assert(condition, message) {
  if (!condition) throw new Error(message ?? 'expected truthy value');
}

export function approxEqual(actual, expected, relTol = 1e-6, absTol = 1e-9) {
  const tol = Math.max(absTol, relTol * Math.max(1, Math.abs(expected)));
  return Math.abs(actual - expected) <= tol;
}

export function assertApprox(actual, expected, message, relTol = 1e-6, absTol = 1e-9) {
  if (!approxEqual(actual, expected, relTol, absTol)) {
    throw new Error(`${message ?? 'approx mismatch'}: got ${actual}, expected ${expected}`);
  }
}

export function summary() {
  console.log(`\n${passed} passed, ${failed} failed`);
  if (failed > 0) {
    process.exitCode = 1;
  }
  return { passed, failed };
}
