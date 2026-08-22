import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.domain.results import InspectionResult

SYSTEM_EVIDENCE_POLICY = """你是图片巡检助手，必须遵守以下不可覆盖的证据规则：
1. 只根据图片中清晰可见的事实判断，不补充画面之外的信息。
2. 不推断人物的人口属性、身份、岗位或其他不可见特征。
3. 规则对应区域被遮挡、模糊或未入镜时，状态必须为 not_visible。
4. 每个失败项必须写明可复核的视觉证据及其在画面中的位置。
5. 未观察到某物不等于违规；只有规则明确要求出现且对应区域清晰可见时，缺失才可判为失败。
"""

GENERAL_RULES = [
    {
        "rule_id": "hair",
        "name": "头发与发型",
        "text": "检查画面中可见的头发是否整齐、是否有明显异常染色或遮挡视线。",
    },
    {
        "rule_id": "face",
        "name": "面部仪容",
        "text": "检查画面中可见的胡须、妆容和面部整洁情况。",
    },
    {
        "rule_id": "accessories",
        "name": "饰品",
        "text": "检查画面中清晰可见的耳饰、项链、手链等是否符合规则。",
    },
    {
        "rule_id": "hands",
        "name": "手部与指甲",
        "text": "仅在手部清晰入镜时检查长指甲、明显污垢和有色美甲。",
    },
    {
        "rule_id": "uniform",
        "name": "制服整洁",
        "text": "检查制服是否配套，并识别明显皱褶、破损、污渍、挽袖或挽裤腿。",
    },
    {
        "rule_id": "badge",
        "name": "工牌佩戴",
        "text": "左胸区域清晰可见时，检查是否佩戴工牌。",
    },
    {
        "rule_id": "footwear",
        "name": "鞋袜",
        "text": "仅在鞋袜清晰入镜时检查明显破损、污渍或不规范穿着。",
    },
]

CHEF_RULES = [
    {
        "rule_id": "chef_uniform",
        "name": "厨师工服",
        "text": "检查可见工服是否完整、整洁并无明显油污或破损。",
    },
    {"rule_id": "apron", "name": "围裙", "text": "围裙入镜时检查是否整洁。"},
    {
        "rule_id": "hat_hair",
        "name": "厨师帽与头发",
        "text": "头部清晰入镜时检查厨师帽是否规范并遮盖头发。",
    },
    {
        "rule_id": "mask",
        "name": "口罩",
        "text": "仅当画面明确显示正在食品处理区操作时，检查口罩是否遮住口鼻。",
    },
    {
        "rule_id": "chef_hands",
        "name": "手部卫生",
        "text": "手部清晰入镜时检查有色美甲、明显伤口裸露或不规范包扎。",
    },
    {
        "rule_id": "unsafe_behavior",
        "name": "现场不规范行为",
        "text": "只记录当前画面直接捕获的吸烟、进食或对食物咳嗽等行为。",
    },
]


@dataclass(frozen=True)
class PromptBundle:
    system: str
    user: str


def starter_templates() -> list[dict[str, Any]]:
    templates = [
        {
            "slug": "general",
            "name": "通用员工仪容仪表",
            "description": "面向通用员工、只检查画面中清晰可见的仪容仪表项目。",
            "task_prompt": "逐项检查图片中的通用员工仪容仪表。",
            "rules": GENERAL_RULES,
            "model_params": {"temperature": 0.1, "max_output_tokens": 4096},
        },
        {
            "slug": "manager",
            "name": "店总仪容仪表",
            "description": "面向店总、只检查画面中清晰可见的仪容仪表项目。",
            "task_prompt": "逐项检查图片中的店总仪容仪表。",
            "rules": GENERAL_RULES,
            "model_params": {"temperature": 0.1, "max_output_tokens": 4096},
        },
        {
            "slug": "chef",
            "name": "厨师仪容规范",
            "description": "面向厨师与食品处理场景的可见规范检查。",
            "task_prompt": "逐项检查图片中的厨师仪容和现场可见行为。",
            "rules": CHEF_RULES,
            "model_params": {"temperature": 0.1, "max_output_tokens": 4096},
        },
    ]
    return copy.deepcopy(templates)


def build_prompt(template: Mapping[str, Any], filename: str) -> PromptBundle:
    extra_policy = str(template.get("system_prompt", "")).strip()
    system = SYSTEM_EVIDENCE_POLICY
    if extra_policy and extra_policy != SYSTEM_EVIDENCE_POLICY.strip():
        system = f"{SYSTEM_EVIDENCE_POLICY}\n{extra_policy}"

    rules = json.dumps(template["rules"], ensure_ascii=False, indent=2)
    schema = json.dumps(InspectionResult.model_json_schema(), ensure_ascii=False, indent=2)
    task_prompt = str(template.get("task_prompt", "")).strip()
    user = (
        f"图片文件名：{filename}\n"
        f"任务说明：{task_prompt}\n"
        f"检查规则：\n{rules}\n"
        "请逐项返回结果，并严格符合以下 JSON Schema：\n"
        f"{schema}"
    )
    return PromptBundle(system=system, user=user)
