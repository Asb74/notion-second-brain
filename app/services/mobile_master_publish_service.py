"""Backward-compatible aliases for the mobile Firebase publish service."""

from app.services.mobile_firebase_publish_service import (
    MobileFirebasePublishError as MobileMasterPublishError,
    MobileFirebasePublishService,
    MobileFirebasePublishSummary as MobileMasterPublishSummary,
)


class MobileMasterPublishService(MobileFirebasePublishService):
    """Compatibility wrapper for the former masters-only service name."""

    def publish(self) -> MobileMasterPublishSummary:
        """Publish the current mobile Firebase dataset using the old method name."""
        return self.publish_all()


__all__ = [
    "MobileMasterPublishError",
    "MobileMasterPublishService",
    "MobileMasterPublishSummary",
]
