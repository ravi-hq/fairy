from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from sprites import SpritesClient, SpriteError

from agent_on_demand.models import (
    Agent,
    AgentVersion,
    APIKey,
    AgentSession,
    AgentSessionLog,
    Environment,
    EnvironmentVersion,
    UserRuntimeKey,
    UserSpritesKey,
)


class APIKeyInline(admin.TabularInline):
    model = APIKey
    extra = 0
    fields = ("key_prefix", "name", "is_active", "created_at", "expires_at")
    readonly_fields = ("key_prefix", "created_at")


class UserRuntimeKeyInline(admin.TabularInline):
    model = UserRuntimeKey
    extra = 0
    fields = ("runtime", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")


class UserSpritesKeyInline(admin.TabularInline):
    model = UserSpritesKey
    extra = 0
    fields = ("created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")


admin.site.unregister(User)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    inlines = list(BaseUserAdmin.inlines) + [
        APIKeyInline,
        UserRuntimeKeyInline,
        UserSpritesKeyInline,
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


class UserRuntimeKeyForm(forms.ModelForm):
    api_key = forms.CharField(
        widget=forms.PasswordInput(render_value=True),
        help_text="The API key for this runtime. Stored encrypted.",
    )

    class Meta:
        model = UserRuntimeKey
        fields = ("user", "runtime", "api_key")

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


@admin.register(UserRuntimeKey)
class UserRuntimeKeyAdmin(admin.ModelAdmin):
    form = UserRuntimeKeyForm
    list_display = ("user", "runtime", "created_at", "updated_at")
    list_filter = ("runtime",)
    search_fields = ("user__email", "runtime")
    readonly_fields = ("created_at", "updated_at")

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("user", "runtime", "api_key")
        return ("user", "runtime", "api_key", "created_at", "updated_at")


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
    search_fields = ("id", "sprite_name", "user__email")
    readonly_fields = (
        "id",
        "user",
        "runtime",
        "prompt",
        "sprite_name",
        "status",
        "exit_code",
        "created_at",
        "updated_at",
    )
    inlines = [AgentSessionLogInline]
    actions = ["terminate_sessions", "purge_sessions"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Terminate selected sessions (destroy Sprites, keep records)")
    def terminate_sessions(self, request, queryset):
        from django.conf import settings

        clients: dict[int, SpritesClient] = {}

        def client_for(user) -> SpritesClient | None:
            if user.pk in clients:
                return clients[user.pk]
            try:
                token = user.sprites_key.get_api_key()
            except UserSpritesKey.DoesNotExist:
                clients[user.pk] = None
                return None
            client = SpritesClient(token=token, base_url=settings.SPRITES_BASE_URL)
            clients[user.pk] = client
            return client

        terminated = 0
        missing_key_users: set[str] = set()
        for session in queryset.exclude(status="terminated"):
            if session.sprite_name:
                client = client_for(session.user)
                if client is None:
                    missing_key_users.add(str(session.user))
                else:
                    try:
                        client.delete_sprite(session.sprite_name)
                    except SpriteError:
                        pass
            session.status = "terminated"
            session.sprite_name = ""
            session.save(update_fields=["status", "sprite_name", "updated_at"])
            terminated += 1
        skipped = queryset.filter(status="terminated").count()
        msg = f"Terminated {terminated} session(s)."
        if skipped:
            msg += f" Skipped {skipped} already terminated."
        if missing_key_users:
            msg += (
                f" Sprite cleanup skipped for {len(missing_key_users)} user(s) "
                f"with no Sprites key: {', '.join(sorted(missing_key_users))}."
            )
            messages.warning(request, msg)
        else:
            messages.success(request, msg)

    @admin.action(description="Purge selected sessions and their logs (terminal only)")
    def purge_sessions(self, request, queryset):
        terminal = ("completed", "failed", "terminated")
        skipped = queryset.exclude(status__in=terminal).count()
        if skipped:
            messages.warning(
                request,
                f"Skipped {skipped} non-terminal session(s); terminate them first.",
            )
        deleted, _ = queryset.filter(status__in=terminal).delete()
        messages.success(request, f"Purged {deleted} session(s) and their logs.")


@admin.register(AgentSessionLog)
class AgentSessionLogAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "stream", "created_at")
    list_filter = ("stream",)
    readonly_fields = ("session", "stream", "data", "created_at")
