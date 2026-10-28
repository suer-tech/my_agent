// Import first: retain OS paths and Node's test-worker marker; set installation values in the test.
const processPaths = new Set([
  "PATH",
  "HOME",
  "TMPDIR",
  "TMP",
  "TEMP",
  "SystemRoot",
  "NODE_TEST_CONTEXT",
]);
for (const name of Object.keys(process.env))
  if (!processPaths.has(name)) delete process.env[name];
