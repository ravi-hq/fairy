from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from agent_on_demand import session_service
from agent_on_demand.models import (
    Agent,
    AgentVersion,
    APIKey,
    AgentSession,
    AgentSessionLog,
    Environment,
    EnvironmentVersion,
    UserBackendCredential,
    UserCredential,
    UserQuota,
    UserSpritesKey,
)
from agent_on_demand.models.auth import CREDENTIAL_ENV_VAR
from agent_on_demand.session_service.backends import BackendClient, BackendError


class APIKeyInline(admin.TabularInline):
    model = APIKey
    extra = 0
    fields = ("key_prefix", "name", "is_active", "created_at", "expires_at")
    readonly_fields = ("key_prefix", "created_at")


class UserCredentialInline(admin.TabularInline):
    model = UserCredential
    extra = 0
    fields = ("kind", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")


class UserSpritesKeyInline(admin.TabularInline):
    model = UserSpritesKey
    extra = 0
    fields = ("created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")


class UserBackendCredentialInline(admin.TabularInline):
    model = UserBackendCredential
    extra = 0
    fields = ("backend", "created_at")
    readonly_fields = ("created_at",)


class UserQuotaInline(admin.StackedInline):
    model = UserQuota
    extra = 0
    fields = ("max_concurrent_sessions", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")


admin.site.unregister(User)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    inlines = list(BaseUserAdmin.inlines) + [
        APIKeyInline,
        UserCredentialInline,
        UserSpritesKeyInline,
        UserBackendCredentialInline,
        UserQuotaInline,
    ]


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    list_display = ("key_prefix", "name", "user", "is_active", "created_at", "expires_at")
    list_filter = ("is_active",)
    search_fields = ("name", "key_prefix", "user__email")
    readonly_fields = ("key_prefix", "key_hash", "created_at")

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("user", "name", "expires_at")
        return ("user", "key_prefix", "key_hash", "name", "is_active", "created_at", "expires_at")

    def save_model(self, request, obj, form, change):
        if not change:
            api_key, raw_key = APIKey.create_key(
                user=form.cleaned_data["user"],
                name=form.cleaned_data["name"],
                expires_at=form.cleaned_data.get("expires_at"),
            )
            obj.pk = api_key.pk
            obj.key_hash = api_key.key_hash
            obj.key_prefix = api_key.key_prefix
            messages.warning(
                request,
                f"API key created. Copy it now — it won't be shown again: {raw_key}",
            )
        else:
            super().save_model(request, obj, form, change)


class UserCredentialForm(forms.ModelForm):
    kind = forms.ChoiceField(
        choices=[(k, k) for k in CREDENTIAL_ENV_VAR],
        help_text="Credential kind (e.g. provider:anthropic). Determines the "
        "env var that gets exported on Sprite startup.",
    )
    value = forms.CharField(
        widget=forms.PasswordInput(render_value=True),
        help_text="The credential value. Stored encrypted.",
    )

    class Meta:
        model = UserCredential
        fields = ("user", "kind", "value")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["value"].initial = self.instance.get_value()

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.set_value(self.cleaned_data["value"])
        if commit:
            instance.save()
        return instance


@admin.register(UserCredential)
class UserCredentialAdmin(admin.ModelAdmin):
    form = UserCredentialForm
    list_display = ("user", "kind", "created_at", "updated_at")
    list_filter = ("kind",)
    search_fields = ("user__email", "kind")
    readonly_fields = ("created_at", "updated_at")

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("user", "kind", "value")
        return ("user", "kind", "value", "created_at", "updated_at")


class UserSpritesKeyForm(forms.ModelForm):
    api_key = forms.CharField(
        widget=forms.PasswordInput(render_value=True),
        help_text="The Sprites API token. Stored encrypted.",
    )

    class Meta:
        model = UserSpritesKey
        fields = ("user", "api_key")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["api_key"].initial = self.instance.get_api_key()

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.set_api_key(self.cleaned_data["api_key"])
        if commit:
            instance.save()
        return instance


@admin.register(UserSpritesKey)
class UserSpritesKeyAdmin(admin.ModelAdmin):
    form = UserSpritesKeyForm
    list_display = ("user", "created_at", "updated_at")
    search_fields = ("user__email",)
    readonly_fields = ("created_at", "updated_at")

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("user", "api_key")
        return ("user", "api_key", "created_at", "updated_at")


# Only "sprites" is wired today. ModalBackend lands in a follow-up plan and
# adds itself here when it does.
BACKEND_CHOICES = [("sprites", "sprites")]


class UserBackendCredentialForm(forms.ModelForm):
    backend = forms.ChoiceField(
        choices=BACKEND_CHOICES,
        help_text="Which session backend this credential targets.",
    )
    token = forms.CharField(
        widget=forms.PasswordInput(render_value=True),
        help_text="The backend API token. Stored encrypted.",
    )

    class Meta:
        model = UserBackendCredential
        fields = ("user", "backend", "token")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["token"].initial = self.instance.get_token()

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.set_token(self.cleaned_data["token"])
        if commit:
            instance.save()
        return instance


@admin.register(UserBackendCredential)
class UserBackendCredentialAdmin(admin.ModelAdmin):
    form = UserBackendCredentialForm
    list_display = ("user", "backend", "created_at")
    list_filter = ("backend",)
    search_fields = ("user__email", "backend")
    readonly_fields = ("created_at",)

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("user", "backend", "token")
        return ("user", "backend", "token", "created_at")


class EnvironmentVersionInline(admin.TabularInline):
    model = EnvironmentVersion
    extra = 0
    fields = ("version", "name", "networking_type", "created_at")
    readonly_fields = ("version", "name", "networking_type", "created_at")


@admin.register(Environment)
class EnvironmentAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "networking_type", "version", "archived_at", "created_at")
    list_filter = ("networking_type",)
    search_fields = ("name", "user__email")
    readonly_fields = ("id", "version", "created_at", "updated_at", "archived_at")
    inlines = [EnvironmentVersionInline]


class AgentVersionInline(admin.TabularInline):
    model = AgentVersion
    extra = 0
    fields = ("version", "name", "model", "runtime", "created_at")
    readonly_fields = ("version", "name", "model", "runtime", "created_at")


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "model", "runtime", "version", "archived_at", "created_at")
    list_filter = ("runtime",)
    search_fields = ("name", "user__email", "description")
    readonly_fields = ("id", "version", "created_at", "updated_at", "archived_at")
    inlines = [AgentVersionInline]


class AgentSessionLogInline(admin.TabularInline):
    model = AgentSessionLog
    extra = 0
    fields = ("stream", "data", "created_at")
    readonly_fields = ("stream", "data", "created_at")


@admin.register(AgentSession)
class AgentSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "runtime", "status", "exit_code", "created_at")
    list_filter = ("runtime", "status")
    search_fields = ("id", "backend_handle", "user__email")
    readonly_fields = (
        "id",
        "user",
        "runtime",
        "prompt",
        "backend_handle",
        "status",
        "exit_code",
        "created_at",
        "updated_at",
    )
    inlines = [AgentSessionLogInline]
    actions = ["terminate_sessions"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Terminate selected sessions (destroy backend handles, keep records)")
    def terminate_sessions(self, request, queryset):
        clients: dict[int, BackendClient | None] = {}

        def client_for(user) -> BackendClient | None:
            if user.pk in clients:
                return clients[user.pk]
            client = session_service.get_client(user)
            clients[user.pk] = client
            return client

        terminated = 0
        missing_key_users: set[str] = set()
        for session in queryset.exclude(status="terminated"):
            handle = session.backend_handle
            if handle:
                client = client_for(session.user)
                if client is None:
                    missing_key_users.add(str(session.user))
                else:
                    try:
                        client.destroy(handle)
                    except BackendError:
                        pass
            session.status = "terminated"
            session.backend_handle = ""
            session.save(update_fields=["status", "backend_handle", "updated_at"])
            terminated += 1
        skipped = queryset.filter(status="terminated").count()
        msg = f"Terminated {terminated} session(s)."
        if skipped:
            msg += f" Skipped {skipped} already terminated."
        if missing_key_users:
            msg += (
                f" Backend cleanup skipped for {len(missing_key_users)} user(s) "
                f"with no backend credentials: {', '.join(sorted(missing_key_users))}."
            )
            messages.warning(request, msg)
        else:
            messages.success(request, msg)


@admin.register(AgentSessionLog)
class AgentSessionLogAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "stream", "created_at")
    list_filter = ("stream",)
    readonly_fields = ("session", "stream", "data", "created_at")
