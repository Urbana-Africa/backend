"""Virtual Try-On (VTON) provider abstraction.

Provides a single interface for generative garment-replacement across multiple
AI providers. Each provider takes a person photo + a garment image and returns
a new image where the person is wearing the garment, preserving the person's
identity, pose and background.

Providers:
  - gemini    : Google Gemini image model ("Nano Banana"). ENABLED.
  - fal       : fal.ai dedicated VTON (IDM-VTON). DISABLED (testing).
  - replicate : Replicate IDM-VTON / OOTDiffusion. DISABLED (testing).

Only providers whose ``enabled`` flag is True and whose API key is configured
can actually run. The frontend renders a temporary model selector; disabled
providers are shown greyed-out.
"""

from __future__ import annotations

import base64
import io
from abc import ABC, abstractmethod

import requests
from django.conf import settings


# Default garment-replacement instruction shared across text-driven providers.
DEFAULT_PROMPT = (
    "Replace the clothing worn by the person in the first image with the "
    "garment shown in the second image. Keep the person's face, hairstyle, "
    "body shape, pose, skin tone, lighting and background completely "
    "unchanged. The garment must fit naturally on the body with realistic "
    "fabric folds, shadows and proportions. Output a single photorealistic "
    "image of the same person now wearing the new garment."
)


class VtonError(Exception):
    """Raised when a try-on generation fails."""


class VtonProvider(ABC):
    """Base class for all virtual try-on providers."""

    key: str = ""
    label: str = ""
    description: str = ""
    enabled: bool = False

    def is_configured(self) -> bool:
        """Whether the provider has the credentials it needs to run."""
        return True

    @abstractmethod
    def generate(
        self,
        person_bytes: bytes,
        person_mime: str,
        garment_bytes: bytes,
        garment_mime: str,
        product=None,
    ) -> bytes:
        """Return PNG/JPEG bytes of the person wearing the garment."""
        raise NotImplementedError


# ──────────────────────────────────────────────────────────────────────────
# Google AI (Gemini / Imagen) — ENABLED
# ──────────────────────────────────────────────────────────────────────────
class GeminiVtonProvider(VtonProvider):
    key = "gemini"
    label = "Google AI (Imagen + Gemini)"
    description = "Imagen 3 for try-on, Gemini for chat/vision."
    enabled = True

    # Imagen 3 for guaranteed image generation (VTON)
    DEFAULT_VTON_MODEL = "imagen-3.0-generate-002"

    # Gemini for multi-modal chat and vision understanding
    DEFAULT_CHAT_MODEL = "gemini-2.0-flash"

    @property
    def VTON_MODEL(self) -> str:
        return getattr(settings, "VTON_GEMINI_MODEL", "") or self.DEFAULT_VTON_MODEL

    @property
    def CHAT_MODEL(self) -> str:
        return getattr(settings, "CHAT_GEMINI_MODEL", "") or self.DEFAULT_CHAT_MODEL

    def is_configured(self) -> bool:
        return bool(getattr(settings, "GEMINI_SECRET_KEY", ""))

    def generate(self, person_bytes, person_mime, garment_bytes, garment_mime, product=None):
        gemini_key = getattr(settings, "GEMINI_SECRET_KEY", "")
        if not gemini_key:
            raise VtonError("Gemini API key is not configured.")

        try:
            from google import genai
            from google.genai import types
        except Exception as exc:  # pragma: no cover - import guard
            raise VtonError(f"google-genai not available: {exc}")

        prompt = DEFAULT_PROMPT
        if product is not None:
            name = getattr(product, "name", "") or ""
            category = getattr(getattr(product, "category", None), "name", "") or ""
            extra = " ".join(p for p in [category, name] if p)
            if extra:
                prompt += f" The garment is a {extra}."

        client = genai.Client(api_key=gemini_key)

        # ── VTON path: Imagen for image generation ──
        if self.VTON_MODEL.startswith("imagen-"):
            try:
                response = client.models.generate_images(
                    model=self.VTON_MODEL,
                    prompt=prompt,
                    config=types.GenerateImagesConfig(
                        number_of_images=1,
                    ),
                )
            except Exception as exc:
                raise VtonError(f"Imagen generation failed: {exc}")

            image_bytes = self._extract_imagen_image(response)
            if not image_bytes:
                raise VtonError("Imagen did not return an image.")
            return image_bytes

        # Fallback: Gemini multi-modal image editing
        try:
            response = client.models.generate_content(
                model=self.VTON_MODEL,
                contents=[
                    types.Part.from_bytes(data=person_bytes, mime_type=person_mime),
                    types.Part.from_bytes(data=garment_bytes, mime_type=garment_mime),
                    prompt,
                ],
            )
        except Exception as exc:
            raise VtonError(f"Gemini generation failed: {exc}")

        image_bytes = self._extract_gemini_image(response)
        if not image_bytes:
            raise VtonError("Gemini did not return an image.")
        return image_bytes

    def chat(self, messages: list[dict], images: list[tuple[bytes, str]] | None = None):
        """
        Multi-modal chat using Gemini.

        Args:
            messages: List of {"role": "user"|"model", "text": str}
            images: Optional list of (bytes, mime_type) tuples to include

        Returns:
            str: The model's text response
        """
        gemini_key = getattr(settings, "GEMINI_SECRET_KEY", "")
        if not gemini_key:
            raise VtonError("Gemini API key is not configured.")

        try:
            from google import genai
            from google.genai import types
        except Exception as exc:
            raise VtonError(f"google-genai not available: {exc}")

        contents = []
        for msg in messages:
            role = msg.get("role", "user")
            text = msg.get("text", "")
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=text)]))

        if images:
            for img_bytes, mime in images:
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_bytes(data=img_bytes, mime_type=mime)],
                    )
                )

        client = genai.Client(api_key=gemini_key)
        try:
            response = client.models.generate_content(
                model=self.CHAT_MODEL,
                contents=contents,
            )
        except Exception as exc:
            raise VtonError(f"Gemini chat failed: {exc}")

        return response.text or ""

    @staticmethod
    def _extract_gemini_image(response) -> bytes | None:
        try:
            candidates = getattr(response, "candidates", None) or []
            for cand in candidates:
                content = getattr(cand, "content", None)
                parts = getattr(content, "parts", None) or []
                for part in parts:
                    inline = getattr(part, "inline_data", None)
                    if inline and getattr(inline, "data", None):
                        data = inline.data
                        # SDK may return raw bytes or base64 string
                        if isinstance(data, (bytes, bytearray)):
                            return bytes(data)
                        if isinstance(data, str):
                            return base64.b64decode(data)
        except Exception:
            return None
        return None

    @staticmethod
    def _extract_imagen_image(response) -> bytes | None:
        try:
            generated_images = getattr(response, "generated_images", None) or []
            for img in generated_images:
                image = getattr(img, "image", None)
                if image:
                    data = getattr(image, "image_bytes", None)
                    if data and isinstance(data, (bytes, bytearray)):
                        return bytes(data)
        except Exception:
            return None
        return None


# ──────────────────────────────────────────────────────────────────────────
# fal.ai IDM-VTON — DISABLED (testing)
# ──────────────────────────────────────────────────────────────────────────
class FalVtonProvider(VtonProvider):
    key = "fal"
    label = "fal.ai (IDM-VTON)"
    description = "Dedicated try-on model. Best photorealism + speed."
    enabled = False

    ENDPOINT = "https://fal.run/fal-ai/idm-vton"

    def is_configured(self) -> bool:
        return bool(getattr(settings, "FAL_KEY", ""))

    @staticmethod
    def _data_uri(data: bytes, mime: str) -> str:
        return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")

    def generate(self, person_bytes, person_mime, garment_bytes, garment_mime, product=None):
        fal_key = getattr(settings, "FAL_KEY", "")
        if not fal_key:
            raise VtonError("FAL_KEY is not configured.")

        payload = {
            "human_image_url": self._data_uri(person_bytes, person_mime),
            "garment_image_url": self._data_uri(garment_bytes, garment_mime),
            "description": (getattr(product, "name", "") or "garment"),
        }
        try:
            resp = requests.post(
                self.ENDPOINT,
                json=payload,
                headers={"Authorization": f"Key {fal_key}"},
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise VtonError(f"fal.ai generation failed: {exc}")

        image_url = (data.get("image") or {}).get("url") if isinstance(data.get("image"), dict) else data.get("image")
        if not image_url:
            raise VtonError("fal.ai did not return an image URL.")
        try:
            img_resp = requests.get(image_url, timeout=60)
            img_resp.raise_for_status()
            return img_resp.content
        except Exception as exc:
            raise VtonError(f"Failed to download fal.ai result: {exc}")


# ──────────────────────────────────────────────────────────────────────────
# Replicate IDM-VTON — DISABLED (testing)
# ──────────────────────────────────────────────────────────────────────────
class ReplicateVtonProvider(VtonProvider):
    key = "replicate"
    label = "Replicate (IDM-VTON)"
    description = "Open VTON models. Cheaper per run, slower cold starts."
    enabled = False

    # cuuupid/idm-vton pinned version.
    MODEL_VERSION = "c871bb9b046607b680449b1c23e3c0c1f085a44e6e6c0f0f6d0d8b8b9e8c2f6e"
    ENDPOINT = "https://api.replicate.com/v1/predictions"

    def is_configured(self) -> bool:
        return bool(getattr(settings, "REPLICATE_API_TOKEN", ""))

    @staticmethod
    def _data_uri(data: bytes, mime: str) -> str:
        return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")

    def generate(self, person_bytes, person_mime, garment_bytes, garment_mime, product=None):
        token = getattr(settings, "REPLICATE_API_TOKEN", "")
        if not token:
            raise VtonError("REPLICATE_API_TOKEN is not configured.")

        payload = {
            "version": self.MODEL_VERSION,
            "input": {
                "human_img": self._data_uri(person_bytes, person_mime),
                "garm_img": self._data_uri(garment_bytes, garment_mime),
                "garment_des": (getattr(product, "name", "") or "garment"),
            },
        }
        headers = {
            "Authorization": f"Token {token}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(self.ENDPOINT, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            prediction = resp.json()
        except Exception as exc:
            raise VtonError(f"Replicate request failed: {exc}")

        # Poll until completion.
        get_url = prediction.get("urls", {}).get("get")
        import time

        for _ in range(60):  # up to ~60s
            status_val = prediction.get("status")
            if status_val == "succeeded":
                break
            if status_val in ("failed", "canceled"):
                raise VtonError(f"Replicate prediction {status_val}.")
            time.sleep(1)
            try:
                poll = requests.get(get_url, headers=headers, timeout=30)
                poll.raise_for_status()
                prediction = poll.json()
            except Exception as exc:
                raise VtonError(f"Replicate polling failed: {exc}")

        output = prediction.get("output")
        image_url = output[0] if isinstance(output, list) and output else output
        if not image_url:
            raise VtonError("Replicate did not return an image.")
        try:
            img_resp = requests.get(image_url, timeout=60)
            img_resp.raise_for_status()
            return img_resp.content
        except Exception as exc:
            raise VtonError(f"Failed to download Replicate result: {exc}")


# ──────────────────────────────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────────────────────────────
_PROVIDER_CLASSES = [GeminiVtonProvider, FalVtonProvider, ReplicateVtonProvider]
_PROVIDERS = {cls.key: cls() for cls in _PROVIDER_CLASSES}

DEFAULT_PROVIDER = "gemini"


def get_provider(key: str | None) -> VtonProvider:
    """Return a provider instance, falling back to the default."""
    provider = _PROVIDERS.get(key) or _PROVIDERS.get(DEFAULT_PROVIDER)
    if provider is None:
        raise VtonError("No try-on provider available.")
    return provider


def list_providers() -> list[dict]:
    """Return metadata for the frontend model selector."""
    return [
        {
            "key": p.key,
            "label": p.label,
            "description": p.description,
            "enabled": bool(p.enabled and p.is_configured()),
            "default": p.key == DEFAULT_PROVIDER,
        }
        for p in _PROVIDERS.values()
    ]
