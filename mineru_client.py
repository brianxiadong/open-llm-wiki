"""MinerU document parser HTTP client."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from exceptions import MineruClientError

logger = logging.getLogger(__name__)


class MineruClient:
    def __init__(self, api_url: str, timeout: float = 300) -> None:
        self._base = api_url.rstrip("/")
        self._timeout = timeout

    def _url(self, path: str) -> str:
        path = path if path.startswith("/") else f"/{path}"
        return f"{self._base}{path}"

    @staticmethod
    def _extract_md(data: dict[str, Any], file_path: str) -> dict[str, Any]:
        """Flatten the nested MinerU response into {md_content: str, ...}."""
        if "md_content" in data:
            return data

        results = data.get("results")
        if isinstance(results, dict):
            for file_result in results.values():
                if isinstance(file_result, dict) and "md_content" in file_result:
                    data["md_content"] = file_result["md_content"]
                    return data

        logger.warning(
            "MinerU response has no md_content for %s, keys=%s",
            file_path,
            list(data.keys()),
        )
        return data

    def parse_file(self, file_path: str) -> dict[str, Any]:
        logger.info("MinerU parse_file path=%s", file_path)
        if not os.path.isfile(file_path):
            raise MineruClientError(f"File not found: {file_path}")

        try:
            with httpx.Client(timeout=self._timeout) as client:
                with open(file_path, "rb") as f:
                    response = client.post(
                        self._url("/file_parse"),
                        files=[("files", (os.path.basename(file_path), f))],
                        data={"return_md": "true"},
                    )
                response.raise_for_status()
                try:
                    data = response.json()
                except json.JSONDecodeError as e:
                    logger.exception("MinerU parse_file invalid JSON path=%s", file_path)
                    raise MineruClientError(f"Invalid JSON response: {e}") from e

                return self._extract_md(data, file_path)
        except httpx.TimeoutException as e:
            logger.exception("MinerU parse_file timeout path=%s", file_path)
            raise MineruClientError(f"Request timed out: {e}") from e
        except httpx.ConnectError as e:
            logger.exception("MinerU parse_file connection error path=%s", file_path)
            raise MineruClientError(f"Connection failed: {e}") from e
        except httpx.HTTPStatusError as e:
            logger.exception(
                "MinerU parse_file HTTP error status=%s path=%s",
                e.response.status_code,
                file_path,
            )
            raise MineruClientError(f"HTTP error {e.response.status_code}: {e}") from e
        except httpx.HTTPError as e:
            logger.exception("MinerU parse_file HTTP error path=%s", file_path)
            raise MineruClientError(str(e)) from e

    def parse_file_async(self, file_path: str) -> str:
        logger.info("MinerU parse_file_async path=%s", file_path)
        if not os.path.isfile(file_path):
            raise MineruClientError(f"File not found: {file_path}")

        try:
            with httpx.Client(timeout=self._timeout) as client:
                with open(file_path, "rb") as f:
                    response = client.post(
                        self._url("/tasks"),
                        files=[("files", (os.path.basename(file_path), f))],
                    )
                response.raise_for_status()
                try:
                    data = response.json()
                except json.JSONDecodeError as e:
                    logger.exception("MinerU parse_file_async invalid JSON path=%s", file_path)
                    raise MineruClientError(f"Invalid JSON response: {e}") from e
        except httpx.TimeoutException as e:
            logger.exception("MinerU parse_file_async timeout path=%s", file_path)
            raise MineruClientError(f"Request timed out: {e}") from e
        except httpx.ConnectError as e:
            logger.exception("MinerU parse_file_async connection error path=%s", file_path)
            raise MineruClientError(f"Connection failed: {e}") from e
        except httpx.HTTPStatusError as e:
            logger.exception(
                "MinerU parse_file_async HTTP error status=%s path=%s",
                e.response.status_code,
                file_path,
            )
            raise MineruClientError(f"HTTP error {e.response.status_code}: {e}") from e
        except httpx.HTTPError as e:
            logger.exception("MinerU parse_file_async HTTP error path=%s", file_path)
            raise MineruClientError(str(e)) from e

        task_id = data.get("task_id") or data.get("id")
        if not task_id:
            logger.error("MinerU parse_file_async missing task id in response: %s", data)
            raise MineruClientError("Response did not contain task_id or id")
        return str(task_id)

    def get_task_status(self, task_id: str) -> dict[str, Any]:
        logger.debug("MinerU get_task_status task_id=%s", task_id)
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.get(self._url(f"/tasks/{task_id}"))
                response.raise_for_status()
                try:
                    return response.json()
                except json.JSONDecodeError as e:
                    logger.exception("MinerU get_task_status invalid JSON task_id=%s", task_id)
                    raise MineruClientError(f"Invalid JSON response: {e}") from e
        except httpx.TimeoutException as e:
            logger.exception("MinerU get_task_status timeout task_id=%s", task_id)
            raise MineruClientError(f"Request timed out: {e}") from e
        except httpx.ConnectError as e:
            logger.exception("MinerU get_task_status connection error task_id=%s", task_id)
            raise MineruClientError(f"Connection failed: {e}") from e
        except httpx.HTTPStatusError as e:
            logger.exception(
                "MinerU get_task_status HTTP error status=%s task_id=%s",
                e.response.status_code,
                task_id,
            )
            raise MineruClientError(f"HTTP error {e.response.status_code}: {e}") from e
        except httpx.HTTPError as e:
            logger.exception("MinerU get_task_status HTTP error task_id=%s", task_id)
            raise MineruClientError(str(e)) from e

    def get_task_result(self, task_id: str) -> dict[str, Any]:
        logger.debug("MinerU get_task_result task_id=%s", task_id)
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.get(self._url(f"/tasks/{task_id}/result"))
                response.raise_for_status()
                try:
                    return response.json()
                except json.JSONDecodeError as e:
                    logger.exception("MinerU get_task_result invalid JSON task_id=%s", task_id)
                    raise MineruClientError(f"Invalid JSON response: {e}") from e
        except httpx.TimeoutException as e:
            logger.exception("MinerU get_task_result timeout task_id=%s", task_id)
            raise MineruClientError(f"Request timed out: {e}") from e
        except httpx.ConnectError as e:
            logger.exception("MinerU get_task_result connection error task_id=%s", task_id)
            raise MineruClientError(f"Connection failed: {e}") from e
        except httpx.HTTPStatusError as e:
            logger.exception(
                "MinerU get_task_result HTTP error status=%s task_id=%s",
                e.response.status_code,
                task_id,
            )
            raise MineruClientError(f"HTTP error {e.response.status_code}: {e}") from e
        except httpx.HTTPError as e:
            logger.exception("MinerU get_task_result HTTP error task_id=%s", task_id)
            raise MineruClientError(str(e)) from e

    def health_check(self) -> bool:
        logger.debug("MinerU health_check")
        try:
            with httpx.Client(timeout=min(30.0, self._timeout)) as client:
                response = client.get(self._url("/health"))
                ok = response.status_code == 200
                logger.info("MinerU health_check status=%s ok=%s", response.status_code, ok)
                return ok
        except httpx.TimeoutException as e:
            logger.warning("MinerU health_check timeout: %s", e)
            return False
        except httpx.ConnectError as e:
            logger.warning("MinerU health_check connection error: %s", e)
            return False
        except httpx.HTTPError as e:
            logger.warning("MinerU health_check HTTP error: %s", e)
            return False
