# Blender Studio

Studio production tools for Blender 4.2+ (Render Matrix: batch-render a camera x model matrix).

## Install (online, with automatic updates)

1. Blender: **Edit > Preferences > System > Network**, enable **Allow Online Access**.
2. **Preferences > Get Extensions > Repositories (top right) > + > Add Remote Repository**
   - URL: `https://tao-weijie.github.io/blender_studio/index.json`
   - Enable **Check for Updates on Startup**
3. Search for **Studio** in Get Extensions and click **Install**.

## Releasing an update

1. Bump `version` in `blender_manifest.toml`.
2. Run `./release.sh`.
3. Commit and push. Blender shows an **Update** button on the next check.
