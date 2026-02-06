import { Card, CardContent, CardHeader } from "../ui/card";

interface Profile {
  nickname: string;
  ai_name: string;
  ai_emoji: string;
  platform: string;
  email?: string | null;
  setup_stage: number;
}

interface ProfileCardProps {
  profile?: Profile;
}

export function ProfileCard({ profile }: ProfileCardProps) {
  return (
    <Card>
      <CardHeader>
        <h2 className="text-sm font-semibold text-slate-900">账号与助手</h2>
      </CardHeader>
      <CardContent className="space-y-2 pt-3 text-sm">
        <p className="text-slate-600">昵称：<span className="text-slate-900">{profile?.nickname || "-"}</span></p>
        <p className="text-slate-600">AI 名称：<span className="text-slate-900">{profile?.ai_name || "-"}</span></p>
        <p className="text-slate-600">AI 表情：<span className="text-slate-900">{profile?.ai_emoji || "-"}</span></p>
        <p className="text-slate-600">平台：<span className="text-slate-900">{profile?.platform || "-"}</span></p>
        <p className="text-slate-600">邮箱：<span className="text-slate-900">{profile?.email || "-"}</span></p>
        <p className="text-slate-600">引导阶段：<span className="text-slate-900">{profile?.setup_stage ?? "-"}</span></p>
      </CardContent>
    </Card>
  );
}
