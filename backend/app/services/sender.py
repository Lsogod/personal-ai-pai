from app.services.platforms import feishu, gewechat, miniapp, onebot, telegram


class UnifiedSender:
    async def send_text(self, platform: str, platform_id: str, text: str) -> None:
        if platform == "wechat":
            await gewechat.send_text(platform_id, text)
        elif platform == "qq":
            await onebot.send_text(platform_id, text)
        elif platform == "telegram":
            await telegram.send_text(platform_id, text)
        elif platform == "feishu":
            await feishu.send_text(platform_id, text)
        elif platform == "miniapp":
            await miniapp.send_text(platform_id, text)

    async def send_image(self, platform: str, platform_id: str, image_url: str) -> None:
        if platform == "wechat":
            await gewechat.send_image(platform_id, image_url)
        elif platform == "qq":
            await onebot.send_image(platform_id, image_url)
        elif platform == "telegram":
            await telegram.send_image(platform_id, image_url)
        elif platform == "feishu":
            await feishu.send_image(platform_id, image_url)
        elif platform == "miniapp":
            await miniapp.send_image(platform_id, image_url)
