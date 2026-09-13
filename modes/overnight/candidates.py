from core.models.candidate import Candidate


def detect_candidates(
    transcript,
    window_seconds=45.0,
    stride_seconds=15.0,
):
    """
    High-recall candidate generator.

    This intentionally does NOT make the final editorial decision.
    It creates overlapping context windows for Qwen to inspect.
    """
    segments = transcript.segments
    if not segments:
        return []

    candidates = []
    candidate_id = 1
    start = segments[0].start
    transcript_end = segments[-1].end

    signal_words = [
        "wow", "what", "no way", "insane", "clutch",
        "haha", "lol", "damn", "crazy", "bro",
        "oh my", "let's go", "lets go", "wait",
    ]

    while start < transcript_end:
        end = min(start + window_seconds, transcript_end)

        included = [
            s for s in segments
            if s.end > start and s.start < end
        ]

        if included:
            text = " ".join(s.text for s in included)
            lowered = text.lower()
            hits = [word for word in signal_words if word in lowered]

            # High-recall baseline: keep every window, with signals increasing
            # the priority. Qwen makes the actual decision.
            score = min(10.0, 4.0 + len(hits) * 0.75)

            candidates.append(
                Candidate(
                    candidate_id=candidate_id,
                    start=start,
                    end=end,
                    score=score,
                    signals=hits,
                    reason="High-recall overlapping transcript window",
                )
            )
            candidate_id += 1

        if end >= transcript_end:
            break

        start += stride_seconds

    return candidates
