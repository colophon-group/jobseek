"use client";

import { useEffect, useRef, useState } from "react";
import { getPostingDetail } from "@/lib/actions/search";
import type { PostingDetail } from "@/lib/actions/search";

interface PostingDetailState {
  detail: PostingDetail | null;
  loading: boolean;
  error: boolean;
  descriptionLoaded: boolean;
}

const EMPTY_STATE: PostingDetailState = {
  detail: null,
  loading: false,
  error: false,
  descriptionLoaded: false,
};

export function usePostingDetail(postingId: string | null): PostingDetailState {
  const [state, setState] = useState<PostingDetailState>(EMPTY_STATE);
  const requestIdRef = useRef(0);

  useEffect(() => {
    const requestId = ++requestIdRef.current;

    setState({
      detail: null,
      loading: Boolean(postingId),
      error: false,
      descriptionLoaded: false,
    });

    if (!postingId) return;

    const locale = document.documentElement.lang || "en";

    getPostingDetail({ postingId, locale })
      .then((detail) => {
        if (requestIdRef.current !== requestId) return;
        if (!detail) {
          setState({
            detail: null,
            loading: false,
            error: true,
            descriptionLoaded: false,
          });
          return;
        }

        const needsDescriptionFetch =
          Boolean(detail.descriptionUrl) && !detail.descriptionHtml;

        setState({
          detail,
          loading: false,
          error: false,
          descriptionLoaded: !needsDescriptionFetch,
        });

        if (!needsDescriptionFetch || !detail.descriptionUrl) return;

        fetch(detail.descriptionUrl)
          .then((response) => (response.ok ? response.text() : null))
          .then((html) => {
            if (requestIdRef.current !== requestId) return;
            setState((previous) => ({
              ...previous,
              detail:
                html && previous.detail
                  ? { ...previous.detail, descriptionHtml: html }
                  : previous.detail,
              descriptionLoaded: true,
            }));
          })
          .catch(() => {
            if (requestIdRef.current !== requestId) return;
            setState((previous) => ({
              ...previous,
              descriptionLoaded: true,
            }));
          });
      })
      .catch(() => {
        if (requestIdRef.current !== requestId) return;
        setState({
          detail: null,
          loading: false,
          error: true,
          descriptionLoaded: false,
        });
      });

    return () => {
      if (requestIdRef.current === requestId) {
        requestIdRef.current += 1;
      }
    };
  }, [postingId]);

  return state;
}
