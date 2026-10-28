// One contract for Authored tree and operational processes. Both sides import the package
// source directly (the CLI always did); a bare specifier would load a second copy through
// node_modules, and in a worktree with a symlinked node_modules that copy lives in another
// checkout — the class then has two identities (B7). The build copies packages/ into the
// runtime next to agent/, so the relative path survives it.
export * from "../../packages/context-window/index.ts";
