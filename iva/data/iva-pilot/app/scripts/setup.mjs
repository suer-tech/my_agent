#!/usr/bin/env node
// Permanent compatibility shim: install.sh, npm and iva config invoke this path.
import { main } from "./setup/main.ts";
void main(import.meta.url);
