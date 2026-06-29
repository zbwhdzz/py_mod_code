import logging
import base64
import io
from typing import Optional
from config.models import CaptchaConfig

logger = logging.getLogger(__name__)

class CaptchaSolver:
    def __init__(self, config: CaptchaConfig):
        self.config = config
        
    async def solve_image(self, image_base64: str) -> Optional[str]:
        if not self.config.enabled or not self.config.api_key:
            return None
        try:
            if self.config.service == "2captcha":
                return await self._solve_2captcha(image_base64)
            elif self.config.service == "anticaptcha":
                return await self._solve_anticaptcha(image_base64)
            elif self.config.service == "tesseract":
                return await self._solve_tesseract(image_base64)
        except Exception as e:
            logger.error(f"验证码识别失败: {e}")
            return None
            
    async def _solve_2captcha(self, image_base64: str) -> Optional[str]:
        logger.info("正在使用 2Captcha 识别验证码...")
        return None
        
    async def _solve_anticaptcha(self, image_base64: str) -> Optional[str]:
        logger.info("正在使用 AntiCaptcha 识别验证码...")
        return None
        
    async def _solve_tesseract(self, image_base64: str) -> Optional[str]:
        try:
            import pytesseract
            from PIL import Image
            image_data = base64.b64decode(image_base64)
            image = Image.open(io.BytesIO(image_data))
            text = pytesseract.image_to_string(image, lang='chi_sim+eng')
            return text.strip()
        except ImportError:
            logger.warning("未安装 pytesseract，无法使用本地OCR")
            return None
        except Exception as e:
            logger.error(f"Tesseract OCR 失败: {e}")
            return None