import deckyPlugin from "@decky/rollup";

// Decky's preset wires up React/JSX, the @decky externals (DFL / SP_REACT) and
// the frontend bundle output at dist/index.js, reading plugin.json for the
// manifest.
export default deckyPlugin();
