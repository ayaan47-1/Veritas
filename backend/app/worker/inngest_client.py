from __future__ import annotations

import inngest

from ..config import settings

inngest_client = inngest.Inngest(
    app_id="veritas",
    is_production=settings.app_env == "production",
)
