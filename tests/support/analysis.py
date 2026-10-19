"""Complete the three S1.analysis tasks that precede every S1.extract (ADR-0011)."""

from __future__ import annotations

ANALYSIS_TEXT = (
    "切望の根にあるのは、変化への静かな憧れと、守ってきた日常を手放せない気持ちの綱引きである。"
    "手に入れたいものは遠く、同時に身近な安らぎも失いたくない。そのため願いは言葉にならないまま、"
    "小さな行動の選び方や視線の向け方に滲み出ている。何を求めるかより、何を恐れて立ち止まるかの方が、"
    "この人物の輪郭を強く形づくっている。願いは具体的な場所や人の姿をとらず、気配として繰り返し現れる。"
    "それは満たされることよりも、満たされない状態を保つことで自分を守る働きにも見える。"
    "だからこそ、ひとつの選択が日常の均衡を揺らす瞬間に、本当の望みがはっきりと姿を現すだろう。"
    "そこには安堵と戸惑いが同時に宿り、先へ進む理由と戻る理由が拮抗している。"
)


def submit_analyses(harness, run_id: str, *, executor_id: str = "dummy") -> None:
    """Claim and accept the three analysis tasks of a new run."""

    for kind in ("desire", "suppression", "conflict"):
        claim = harness.claim_task(run_id, f"S1.analysis-{kind}", executor_id=executor_id)
        result = harness.submit(claim["ticket"], ANALYSIS_TEXT[:360].rsplit("。", 1)[0] + "。")
        assert result.accepted, result
