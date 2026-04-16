from httpx import AsyncClient


async def test_health(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_run_invalid_runtime(client: AsyncClient):
    resp = await client.post("/run", json={
        "runtime": "invalid",
        "prompt": "hello",
        "api_key": "fake",
    })
    assert resp.status_code == 400
    assert "Unknown runtime" in resp.json()["detail"]


async def test_run_missing_fields(client: AsyncClient):
    resp = await client.post("/run", json={"runtime": "claude"})
    assert resp.status_code == 422
