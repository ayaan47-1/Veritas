from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])
# Login and callback handled by Clerk frontend SDK
