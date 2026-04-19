from ninja import Router, Schema


class HealthOut(Schema):
    status: str


router = Router(auth=None)


@router.get("/health", response=HealthOut)
def health(request):
    return {"status": "ok"}
