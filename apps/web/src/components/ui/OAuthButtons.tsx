import { Trans } from "@lingui/react/macro";
import { Github } from "lucide-react";
import { GoogleIcon } from "@/components/icons/GoogleIcon";
import { LinkedInIcon } from "@/components/icons/LinkedInIcon";
import { Button } from "@/components/ui/Button";

type OAuthButtonsProps = {
  onOAuth: (provider: "github" | "google" | "linkedin") => void;
};

export function OAuthButtons({ onOAuth }: OAuthButtonsProps) {
  return (
    <>
      <div className="relative my-6">
        <div className="absolute inset-0 flex items-center">
          <div className="w-full border-t border-divider" />
        </div>
        <div className="relative flex justify-center text-sm">
          <span className="bg-surface px-2 text-muted">
            <Trans id="auth.divider.or" comment="Divider between form and OAuth buttons">or</Trans>
          </span>
        </div>
      </div>
      <div className="flex flex-col gap-3">
        <Button variant="outline" className="w-full gap-2" onClick={() => onOAuth("github")}>
          <Github size={20} />
          <Trans id="auth.oauth.github" comment="GitHub OAuth button">Continue with GitHub</Trans>
        </Button>
        <Button variant="outline" className="w-full gap-2" onClick={() => onOAuth("google")}>
          <GoogleIcon />
          <Trans id="auth.oauth.google" comment="Google OAuth button">Continue with Google</Trans>
        </Button>
        <Button variant="outline" className="w-full gap-2" onClick={() => onOAuth("linkedin")}>
          <LinkedInIcon />
          <Trans id="auth.oauth.linkedin" comment="LinkedIn OAuth button">Continue with LinkedIn</Trans>
        </Button>
      </div>
    </>
  );
}
