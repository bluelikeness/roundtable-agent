/**
 * 첨부·봇이 만든 파일 보기 (docs/05-ui.md 3장 files). 허브 파일 보관함(/api/files/<id>)만 연다.
 * 이미지는 작은 그림, 영상·소리는 재생기, 그 밖은 이름·크기 링크(내려받기).
 */
import { useState } from "react";
import { fileSrc } from "../../api/rest";
import type { FileRef } from "../../api/types";

export function humanSize(n: number): string {
  if (n < 1024) return `${n}B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)}KB`;
  return `${(n / 1024 / 1024).toFixed(1)}MB`;
}

// 허브가 바로 보기(inline)로 주는 종류와 맞춘다(hub/app/files.py _INLINE)
const IMAGE = /^image\/(png|jpeg|gif|webp|avif)$/;

export function FileList({ files }: { files: FileRef[] }) {
  const [zoom, setZoom] = useState<string | null>(null);
  const shown = files.map((f) => ({ f, src: fileSrc(f.id) })).filter((x): x is { f: FileRef; src: string } => !!x.src);
  if (!shown.length) return null;
  return (
    <>
      <div className="files" data-testid="files">
        {shown.map(({ f, src }) => {
          if (IMAGE.test(f.mime)) {
            return (
              <button key={f.id} className="thumb" onClick={() => setZoom(src)} aria-label={`이미지 확대: ${f.name}`}>
                <img src={src} alt={f.name} loading="lazy" />
              </button>
            );
          }
          if (f.mime.startsWith("video/")) {
            return (
              <figure key={f.id} className="file-media">
                <video src={src} controls preload="metadata" />
                <figcaption>
                  <a href={src} download={f.name}>
                    {f.name}
                  </a>{" "}
                  <span className="muted">{humanSize(f.size)}</span>
                </figcaption>
              </figure>
            );
          }
          if (f.mime.startsWith("audio/")) {
            return (
              <figure key={f.id} className="file-media">
                <audio src={src} controls preload="metadata" />
                <figcaption>{f.name}</figcaption>
              </figure>
            );
          }
          return (
            <a key={f.id} className="file-chip" href={src} download={f.name}>
              📎 {f.name} <span className="muted">{humanSize(f.size)}</span>
            </a>
          );
        })}
      </div>
      {zoom && (
        <div className="lightbox" role="dialog" aria-label="이미지 확대" onClick={() => setZoom(null)}>
          <img src={zoom} alt="" />
        </div>
      )}
    </>
  );
}
