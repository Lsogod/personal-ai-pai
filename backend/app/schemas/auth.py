from pydantic import BaseModel, EmailStr


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MiniappLoginRequest(BaseModel):
    code: str
    nickname: str | None = None


class MiniappTokenResponse(TokenResponse):
    openid: str
