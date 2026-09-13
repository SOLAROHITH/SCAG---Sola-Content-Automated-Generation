from pathlib import Path
from solarohith.pipeline import run_pipeline as run_overnight_pipeline


class OvernightPipeline:
    """Compatibility wrapper around the production SCAG overnight pipeline."""

    def __init__(self, config="config.yaml"):
        self.config = Path(config).resolve()

    def run(self, source, project_name=None, progress=print):
        root = self.config.parent
        return run_overnight_pipeline(
            source,
            root,
            self.config,
            progress=progress,
            project_name=project_name,
        )
