from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from agent_on_demand.models import (
    Agent,
    AgentSession,
    AgentSessionLog,
    APIKey,
    Environment,
    UserSpritesKey,
)
from agent_on_demand.ui.forms import APIKeyCreateForm, RegisterForm, SpritesKeyForm


def register(request):
    if request.user.is_authenticated:
        return redirect("ui-dashboard")

    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()

            usk = UserSpritesKey(user=user)
            usk.set_api_key(form.cleaned_data["sprites_api_key"])
            usk.save()

            _, raw_key = APIKey.create_key(user=user, name="Onboarding key")

            login(request, user)
            request.session["onboarding_raw_key"] = raw_key
            return redirect("ui-welcome")
    else:
        form = RegisterForm()

    return render(request, "ui/register.html", {"form": form})


@login_required(login_url="/ui/login")
def welcome(request):
    raw_key = request.session.pop("onboarding_raw_key", None)
    if not raw_key:
        return redirect("ui-dashboard")

    api_base = request.build_absolute_uri("/").rstrip("/")
    return render(
        request,
        "ui/welcome.html",
        {"raw_key": raw_key, "api_base": api_base},
    )


@login_required(login_url="/ui/login")
def dashboard(request):
    has_sprites_key = UserSpritesKey.objects.filter(user=request.user).exists()
    counts = {
        "agents": Agent.objects.filter(user=request.user, archived_at__isnull=True).count(),
        "environments": Environment.objects.filter(
            user=request.user, archived_at__isnull=True
        ).count(),
        "sessions": AgentSession.objects.filter(user=request.user).count(),
        "api_keys": APIKey.objects.filter(user=request.user, is_active=True).count(),
    }
    return render(
        request,
        "ui/dashboard.html",
        {"has_sprites_key": has_sprites_key, "counts": counts},
    )


@login_required(login_url="/ui/login")
def sprites_key(request):
    existing = UserSpritesKey.objects.filter(user=request.user).first()

    if request.method == "POST":
        form = SpritesKeyForm(request.POST)
        if form.is_valid():
            usk = existing or UserSpritesKey(user=request.user)
            usk.set_api_key(form.cleaned_data["api_key"])
            usk.save()
            messages.success(request, "Sprites token saved.")
            return redirect("ui-sprites-key")
    else:
        form = SpritesKeyForm()

    return render(
        request,
        "ui/sprites_key.html",
        {"form": form, "has_existing": existing is not None},
    )


@login_required(login_url="/ui/login")
def api_keys(request):
    new_raw_key = None
    if request.method == "POST":
        form = APIKeyCreateForm(request.POST)
        if form.is_valid():
            _, new_raw_key = APIKey.create_key(
                user=request.user,
                name=form.cleaned_data["name"],
                expires_at=form.cleaned_data["expires_at"],
            )
            messages.success(request, "API key created — copy it now, it won't be shown again.")
            form = APIKeyCreateForm()
    else:
        form = APIKeyCreateForm()

    keys = APIKey.objects.filter(user=request.user).order_by("-created_at")
    return render(
        request,
        "ui/api_keys.html",
        {"form": form, "keys": keys, "new_raw_key": new_raw_key},
    )


@require_POST
@login_required(login_url="/ui/login")
def api_key_revoke(request, key_id):
    try:
        key = APIKey.objects.get(pk=key_id, user=request.user)
    except APIKey.DoesNotExist as exc:
        raise Http404("API key not found") from exc
    key.is_active = False
    key.save(update_fields=["is_active"])
    messages.success(request, f"Revoked {key.key_prefix}…")
    return redirect("ui-api-keys")


@login_required(login_url="/ui/login")
def agents_list(request):
    agents = Agent.objects.filter(user=request.user).order_by("-created_at")
    return render(request, "ui/agents_list.html", {"agents": agents})


@login_required(login_url="/ui/login")
def agent_detail(request, agent_id):
    agent = get_object_or_404(Agent, pk=agent_id, user=request.user)
    return render(request, "ui/agent_detail.html", {"agent": agent})


@login_required(login_url="/ui/login")
def environments_list(request):
    envs = Environment.objects.filter(user=request.user).order_by("-created_at")
    return render(request, "ui/environments_list.html", {"envs": envs})


@login_required(login_url="/ui/login")
def environment_detail(request, environment_id):
    env = get_object_or_404(Environment, pk=environment_id, user=request.user)
    return render(request, "ui/environment_detail.html", {"env": env})


@login_required(login_url="/ui/login")
def sessions_list(request):
    sessions = (
        AgentSession.objects.filter(user=request.user)
        .select_related("agent", "environment")
        .order_by("-created_at")
    )
    return render(request, "ui/sessions_list.html", {"sessions": sessions})


@login_required(login_url="/ui/login")
def session_detail(request, session_id):
    session = get_object_or_404(
        AgentSession.objects.select_related("agent", "environment"),
        pk=session_id,
        user=request.user,
    )
    logs = AgentSessionLog.objects.filter(session=session).order_by("id")
    return render(
        request,
        "ui/session_detail.html",
        {"session": session, "logs": logs, "resources": session.resources.all()},
    )
