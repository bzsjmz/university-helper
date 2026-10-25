"""Reject non-answers before reuse, persistence, or form filling.

This detects explicit abstentions, not correctness. A nonempty model response
has never been evidence that the platform will mark an answer correct.
"""
import re


_ABSTENTION = re.compile(
    r"^(?:(?:答案|答|answer)\s*[:：]\s*)?"
    r"(?:(?:抱歉|很抱歉|对不起|sorry)[，,。:：!！\s]*)?"
    r"(?:无法(?:确定|识别|判断|回答|解答|作答|提供)"
    r"(?=$|[，,。.!！?？：:\s]|正确|具体|该|此|题|这|答|问|相|其|所)|不能(?:确定|识别|判断|回答)|"
    r"不(?:知道|清楚|确定)|未(?:能|找到)(?:确定|识别|答案)|"
    r"(?:题目|题干|问题|所提供的题目|提供的题目|当前题目|该题目|该问题|题目中)"
    r".{0,30}(?:乱码|加密文本|无法识别|信息不足|未提供|缺少)|"
    r"(?:信息|上下文|题目信息)(?:不足|不完整)|"
    r"(?:请|需要)(?:提供|补充).{0,20}(?:题目|题干|信息|上下文)|"
    r"(?:i\s+(?:cannot|can't|can not|am unable to|don't know)|"
    r"unable to|cannot|can't)\b|unknown\s*$|n/?a\s*$)",
    re.IGNORECASE,
)


def is_usable_answer(answer: object) -> bool:
    if not isinstance(answer, str) or not answer.strip():
        return False
    # Check each blank/line too, so a multi-blank answer cannot hide an abstention.
    return all(not _ABSTENTION.search(line.strip(' \t\r\n"\'`*'))
               for line in answer.splitlines() if line.strip())


def has_unanswered_text(questions: list[dict]) -> bool:
    """Unanswered written questions must never trigger retry-forced submission."""
    return any(q.get('type') in ('shortanswer', 'completion') and
               (q.get(f'answerSource{q["id"]}') != 'cover' or
                not is_usable_answer(q.get('answerField', {}).get(f'answer{q["id"]}')))
               for q in questions)
