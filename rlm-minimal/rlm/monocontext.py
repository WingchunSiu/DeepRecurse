from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Optional, List

import boto3


@dataclass
class Monocontext:
    """
    Super-barebones bucket-backed context using a manifest.json.

    The manifest is a JSON array of objects:
    {
      "segment": "segment_000001.log",
      "start_line": 0,
      "line_count": 1000
    }

    - len(context) returns total line count.
    - context[start:stop] returns text for that line range.
    """

    bucket: str
    prefix: str = ""
    manifest_name: str = "manifest.json"
    region: Optional[str] = None
    encoding: str = "utf-8"
    errors: str = "strict"
    client: Optional[object] = None
    enable_logging: bool = False

    def __post_init__(self) -> None:
        if self.client is None:
            session = boto3.session.Session(region_name=self.region)
            self.client = session.client("s3")
        self._segments: Optional[List[dict]] = None
        self._total_lines: Optional[int] = None

    def _log(self, message: str) -> None:
        if self.enable_logging:
            print(f"[Monocontext] {message}")

    def __len__(self) -> int:
        if self._total_lines is None:
            self._log("length requested; manifest not loaded yet, loading now")
            self._load_manifest()
        return self._total_lines

    def __getitem__(self, key: slice) -> str:
        if not isinstance(key, slice):
            raise TypeError("Monocontext only supports slicing with [start:stop].")
        if key.step not in (None, 1):
            raise ValueError("Monocontext does not support slice steps.")

        if self._segments is None:
            self._log("slice requested; manifest not loaded yet, loading now")
            self._load_manifest()

        total_lines = len(self)
        start = 0 if key.start is None else key.start
        stop = total_lines if key.stop is None else key.stop

        if start < 0:
            start = total_lines + start
        if stop < 0:
            stop = total_lines + stop

        start = max(0, min(start, total_lines))
        stop = max(0, min(stop, total_lines))

        if stop <= start:
            self._log(f"slice[{start}:{stop}] -> empty")
            return ""

        self._log(f"slice[{start}:{stop}] reading from S3")
        parts: List[str] = []
        touched_segments = 0
        start_time = time.time()

        for seg in self._segments or []:
            seg_start = int(seg["start_line"])
            seg_count = int(seg["line_count"])
            seg_end = seg_start + seg_count

            if seg_end <= start:
                continue
            if seg_start >= stop:
                break

            local_start = max(0, start - seg_start)
            local_stop = min(seg_count, stop - seg_start)

            if local_stop <= local_start:
                continue

            key_name = self._full_key(seg["segment"])
            response = self.client.get_object(Bucket=self.bucket, Key=key_name)
            body = response["Body"].read().decode(self.encoding, errors=self.errors)
            touched_segments += 1

            lines = body.splitlines(keepends=True)
            parts.append("".join(lines[local_start:local_stop]))

        result = "".join(parts)
        elapsed = time.time() - start_time
        self._log(
            f"slice[{start}:{stop}] done; segments={touched_segments}, chars={len(result)}, took={elapsed:.2f}s"
        )
        return result

    def _manifest_key(self) -> str:
        if self.prefix:
            return f"{self.prefix}{self.manifest_name}"
        return self.manifest_name

    def _full_key(self, segment_key: str) -> str:
        if self.prefix and not segment_key.startswith(self.prefix):
            return f"{self.prefix}{segment_key}"
        return segment_key

    def _load_manifest(self) -> None:
        start_time = time.time()
        manifest_key = self._manifest_key()
        self._log(f"loading manifest s3://{self.bucket}/{manifest_key}")
        response = self.client.get_object(Bucket=self.bucket, Key=self._manifest_key())
        raw = response["Body"].read().decode(self.encoding, errors=self.errors)
        segments = json.loads(raw)

        segments.sort(key=lambda s: int(s["start_line"]))
        self._segments = segments

        if not segments:
            self._total_lines = 0
        else:
            last = segments[-1]
            self._total_lines = int(last["start_line"]) + int(last["line_count"])
        elapsed = time.time() - start_time
        self._log(
            f"manifest loaded; segments={len(segments)}, total_lines={self._total_lines}, took={elapsed:.2f}s"
        )
