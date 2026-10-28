"""Separate official-client logins. No password or OAuth-token ingestion surface."""
from pathlib import Path
import secrets
import time

from .files import atomic_json, atomic_text, read_json

SUPPORTED={'codex','claude-code'}
# Remove ambient credentials/routing so an isolated login cannot silently use
# a different local API key, OAuth token or cloud backend.
UNSET={
 'codex':['OPENAI_API_KEY','CODEX_API_KEY','OPENAI_BASE_URL','CHATGPT_BASE_URL','CODEX_HOME'],
 'claude-code':['ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','CLAUDE_CODE_OAUTH_TOKEN',
  'ANTHROPIC_BASE_URL','CLAUDE_CONFIG_DIR','CLAUDE_CODE_USE_BEDROCK','CLAUDE_CODE_USE_VERTEX',
  'CLAUDE_CODE_USE_FOUNDRY','ANTHROPIC_CUSTOM_HEADERS'],
}


def profile_environment(settings):
    directory=settings.get('account_dir')
    if not directory:return {},[]
    provider=settings['provider']
    if provider not in SUPPORTED:raise ValueError('Independent login is supported for Codex and Claude Code')
    directory=Path(directory).expanduser().resolve()
    return {('CODEX_HOME' if provider=='codex' else 'CLAUDE_CONFIG_DIR'):str(directory)},UNSET[provider][:]


def apply_profile(spec,settings):
    env,unset=profile_environment(settings)
    if env:
        spec.setdefault('env',{}).update(env);spec['unset_env']=unset
    return spec


class Accounts:
    def __init__(self,project):self.root=Path(project).resolve()/'.foreman/accounts'
    def path(self,aid):
        if not isinstance(aid,str) or len(aid)!=12 or any(c not in '0123456789abcdef' for c in aid):
            raise ValueError('Invalid account profile ID')
        return self.root/aid
    def get(self,aid):return read_json(self.path(aid)/'profile.json')
    def list(self):return [read_json(p) for p in sorted(self.root.glob('*/profile.json'))]
    def directory(self,aid,provider):
        p=self.get(aid)
        if p['provider']!=provider:raise ValueError('Account profile belongs to another client')
        return self.path(aid)/'client'
    def create(self,provider,label):
        if provider not in SUPPORTED:raise ValueError('Choose Codex or Claude Code for an independent account')
        if not isinstance(label,str) or not label.strip() or len(label)>100:raise ValueError('Account name must contain 1..100 characters')
        aid=secrets.token_hex(6);root=self.path(aid);directory=root/'client'
        directory.mkdir(parents=True,mode=0o700)
        # The official client owns all credentials. We never copy the default
        # login or read generated auth files back into the app or API responses.
        root.chmod(0o700)
        if provider=='codex':
            atomic_text(directory/'config.toml','forced_login_method = "chatgpt"\ncli_auth_credentials_store = "file"\n')
        else:atomic_json(directory/'settings.json',{'forceLoginMethod':'claudeai'})
        profile={'id':aid,'provider':provider,'label':label.strip(),'created_at':time.time(),
                 'authentication':'official_client','login_verified':False}
        atomic_json(root/'profile.json',profile)
        return profile
