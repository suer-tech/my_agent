// Uses the actual checked-out Iva reader, without installing Iva or any services.
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const checkout = resolve(process.argv[2] || 'upstream/iva-agent');
const { readPlugin } = await import(pathToFileURL(resolve(checkout, 'agent/lib/plugin-reader.ts')).href);
const report = await readPlugin(resolve('plugin/family-finance'));
console.log(JSON.stringify(report, null, 2));
if (!report.manifest || report.diagnostics?.length) process.exitCode = 1;
