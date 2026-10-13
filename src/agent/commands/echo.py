"""Echo: say back what was said. Free -- it never touches the model."""


async def run(plow, http, text):
    return f"hello world. You said: {text}" if text else "hello world."
