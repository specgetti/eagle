from fastapi import APIRouter

from eagle.api import admin, auth, integrations, reviews, sso, tracking, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(sso.router)
api_router.include_router(users.router)
api_router.include_router(reviews.router)
api_router.include_router(reviews.internal_router)
api_router.include_router(admin.router)
api_router.include_router(integrations.router)
api_router.include_router(tracking.router)
