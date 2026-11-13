Copied from labs/ by build.py on 2026-11-13 at 5609a755; edit labs, then rebuild.

    python3 plugins/agentic-setup/build.py           # rebuild
    python3 plugins/agentic-setup/build.py --check   # fails when this folder is stale

Not copies: run.py and this file. The layout is labs/'s on purpose: the code finds its neighbours by relative path.
