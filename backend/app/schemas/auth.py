from typing import Literal

from pydantic import BaseModel, EmailStr


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    confirm_password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class SendEmailCodeRequest(BaseModel):
    email: EmailStr
    purpose: Literal["register", "login", "reset_password"]


class SendEmailCodeResponse(BaseModel):
    ok: bool = True
    message: str = "验证码已发送，请注意查收。"
    expire_seconds: int = 600
    cooldown_seconds: int = 60


class RegisterWithCodeRequest(BaseModel):
    email: EmailStr
    password: str
    confirm_password: str
    code: str


class LoginWithCodeRequest(BaseModel):
    email: EmailStr
    code: str


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str
    new_password: str
    confirm_password: str


class ActionResponse(BaseModel):
    ok: bool = True
    message: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MiniappLoginRequest(BaseModel):
    code: str
    nickname: str | None = None


class MiniappTokenResponse(TokenResponse):
    openid: str
