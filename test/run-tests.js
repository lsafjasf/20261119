import { summary } from './harness.js';
import { runFftTests } from './fft.test.js';
import { runWindowTests } from './windows.test.js';
import { runReassignmentTests } from './reassigned.test.js';
import { runMetricsTests } from './metrics.test.js';
import { runRenderTests } from './render.test.js';
import { runAppSmokeTests } from './app.smoke.test.js';

runFftTests();
runWindowTests();
runReassignmentTests();
runMetricsTests();
runRenderTests();
await runAppSmokeTests();
summary();
