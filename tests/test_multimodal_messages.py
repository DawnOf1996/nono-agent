from nono_agent.core.types import UserMessage
from nono_agent.providers._openai_chat_completions_utils import normalize_openai_messages
from nono_agent.providers._openai_responses_utils import normalize_responses_input


def test_openai_chat_converts_anthropic_image_blocks():
    msgs = [
        UserMessage(
            message={
                "role": "user",
                "content": [
                    {"type": "text", "text": "看这张图"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "abc",
                        },
                    },
                ],
            }
        )
    ]
    out = normalize_openai_messages(msgs)
    assert out[0]["role"] == "user"
    assert out[0]["content"] == [
        {"type": "text", "text": "看这张图"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
    ]


def test_responses_input_includes_images():
    msgs = [
        UserMessage(
            message={
                "role": "user",
                "content": [
                    {"type": "text", "text": "看这张图"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": "xyz",
                        },
                    },
                ],
            }
        )
    ]
    items = normalize_responses_input(msgs)
    assert items == [
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "看这张图"},
                {"type": "input_image", "image_url": "data:image/jpeg;base64,xyz"},
            ],
        }
    ]
