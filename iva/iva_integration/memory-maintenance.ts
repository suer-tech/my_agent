// Deterministic upstream maintenance without attaching or pushing a remote.
import {writeVaultGraph} from '../data/iva-pilot/app/scripts/memory/graph.ts';
import {commitVaultSweep} from '../data/iva-pilot/app/agent/lib/vault-commit.ts';
const vault=process.env.ASSISTANT_VAULT_DIR!;
writeVaultGraph(vault);
const result=await commitVaultSweep('memory: local scheduled maintenance',vault);
if(!result.ok)process.exitCode=1;
