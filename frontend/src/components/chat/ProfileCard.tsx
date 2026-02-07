import { Card, CardContent, CardHeader } from "../ui/card";
import { User } from "../ui/icons";

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

const infoItems = (profile?: Profile) => [
  { label: "昵称", value: profile?.nickname },
  { label: "AI 名称", value: profile?.ai_name },
  { label: "AI 表情", value: profile?.ai_emoji },
  { label: "平台", value: profile?.platform },
  { label: "邮箱", value: profile?.email },
  { label: "引导阶段", value: profile?.setup_stage?.toString() },
];

export function ProfileCard({ profile }: ProfileCardProps) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent/10 text-accent">
            <User size={20} />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-content">账号信息</h2>
            <p className="text-xs text-content-tertiary">管理你的个人资料与 AI 设置</p>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          {infoItems(profile).map(({ label, value }) => (
            <div
              key={label}
              className="flex items-center justify-between py-2 border-b border-border last:border-0"
            >
              <span className="text-sm text-content-secondary">{label}</span>
              <span className="text-sm font-medium text-content">{value || "-"}</span>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
