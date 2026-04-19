---
stoplight: true
---

# API Reference

This page provides an interactive API reference powered by [Stoplight Elements](https://stoplight.io/open-source/elements). Browse all endpoints, view request/response schemas, and make live requests directly against your Agent on Demand deployment.

<div>
  <elements-api
    apiDescriptionUrl="openapi.json"
    router="hash"
    layout="stacked"
    tryItCredentialsPolicy="include"
  ></elements-api>
</div>

---

The `openapi.json` file is written into `site/docs/api/` by the docs workflow at build time. For local preview, generate it with:

```bash
uv run python -m scripts.validate_openapi --export site/docs/api/openapi.json
```

This file is not committed to the repository.
