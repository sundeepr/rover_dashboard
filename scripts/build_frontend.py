"""Build the existing vanilla frontend; no Vite/router dependency is needed."""
from pathlib import Path
import shutil
root = Path(__file__).resolve().parents[1]
(root / 'dist').mkdir(exist_ok=True)
for name in ('index.html', 'app.js', 'styles.css'):
    shutil.copy2(root / name, root / 'dist' / name)
