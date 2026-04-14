import base64
import logging
from typing import Optional

import httpx

from app.config import settings
from app.services.minio_service import minio_service

logger = logging.getLogger(__name__)


class AiService:
    """Calls a vLLM-hosted model (OpenAI-compatible API) to describe snags."""

    def __init__(self) -> None:
        self.base_url = settings.VLLM_BASE_URL.rstrip("/")
        self.api_key = settings.VLLM_API_KEY
        self.model = settings.VLLM_MODEL

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def describe_snag(
        self,
        item_name: str,
        category: str,
        room_label: str,
        image_minio_key: Optional[str] = None,
        image_base64: Optional[str] = None,
    ) -> str:
        """
        Use a vision-language model to describe a snag from an image.
        Mirrors the prompt from the Android app's OllamaService.
        """
        # Get image as base64
        b64_data: Optional[str] = image_base64
        if not b64_data and image_minio_key:
            img_bytes, _ = minio_service.get_object(image_minio_key)
            b64_data = base64.b64encode(img_bytes).decode("utf-8")

        prompt = (
            f"You are a construction quality inspector. Analyze this image of a "
            f"'{item_name}' (category: {category}) in the {room_label}.\n\n"
            f"Describe the snag/defect you see in 1-2 concise sentences. "
            f"Focus on: what the defect is, its apparent severity, and any "
            f"recommended action. If the image is unclear, say so."
        )

        messages: list[dict] = []
        if b64_data:
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64_data}"
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            )
        else:
            messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 256,
            "temperature": 0.3,
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                    headers=self._build_headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except Exception as exc:
            logger.error("AI describe_snag failed: %s", exc)
            raise RuntimeError(f"AI service error: {exc}") from exc

    async def analyze_video_frame(
        self, frame_base64: str, context: str = ""
    ) -> str:
        """Analyze a single video frame for construction defects."""
        prompt = (
            "You are a construction quality inspector. Analyze this video frame "
            "from a site walkthrough. Describe any visible defects, quality issues, "
            "or items of concern in 1-2 concise sentences."
        )
        if context:
            prompt += f"\nContext: {context}"

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{frame_base64}"
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 256,
            "temperature": 0.3,
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                    headers=self._build_headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except Exception as exc:
            logger.error("AI analyze_video_frame failed: %s", exc)
            raise RuntimeError(f"AI service error: {exc}") from exc


ai_service = AiService()
