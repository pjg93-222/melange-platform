"""First-install and explicitly approved legacy first-password invitations."""
from pathlib import Path


def legacy_secret_path(data_root, state_root, owner, *, separate_data=False):
    """Require the explicit existing district store; never create a new secret."""
    data, state = Path(data_root).resolve(), Path(state_root).resolve()
    if owner != 'admin' or type(separate_data) is not bool or (data != state and not separate_data) or not data.is_dir() or not state.is_dir():
        raise ValueError('Approved legacy Terrier data and state must use the same district directory')
    if separate_data and (data == state or data.is_relative_to(state) or state.is_relative_to(data)):
        raise ValueError('GitHub district data must be separate from existing owner state')
    for name in ('accounts.sqlite3', '.secret'):
        file = state / name
        if file.is_symlink() or not file.is_file() or file.resolve().parent != state:
            raise ValueError('Existing Terrier accounts and secret must remain in the selected district directory')
    return state / '.secret'


def legacy_owner_invitation(state_root, owner):
    """Let the sole approved passwordless legacy owner choose a first password.

    Existing usable passwords never change. Raw invitations exist only in memory;
    the existing hash-only invitation system expires the previous launch's link.
    """
    from datetime import timedelta
    import hashlib
    from django.contrib.auth.models import User
    from django.db import connections
    from django.utils import timezone
    from server.research.models import Invitation, AuditEvent
    from server.research.views import create_invitation
    from desktop_migrations import migrate_sqlite

    connection = connections['default']
    if owner != 'admin' or connection.vendor != 'sqlite' or connection.in_atomic_block:
        raise RuntimeError('Legacy first-password setup requires the explicitly approved SQLite owner')

    def candidate():
        user = User.objects.filter(username=owner).first()
        if user is None:
            raise RuntimeError('The approved legacy owner account is missing')
        if user.has_usable_password():
            return None
        if User.objects.count() != 1 or not (user.is_active and user.is_staff and user.is_superuser):
            raise RuntimeError('Legacy first-password setup refuses ambiguous or restricted accounts')
        return user

    if candidate() is None:
        return None
    result = {'path': None, 'identity': None}

    def identity(user):
        return (user.pk, user.username, user.password, user.is_active, user.is_staff, user.is_superuser,
                tuple(user.groups.order_by('pk').values_list('pk', flat=True)),
                tuple(user.user_permissions.order_by('pk').values_list('pk', flat=True)))

    def issue():
        with connection.cursor() as cursor:
            cursor.execute('BEGIN IMMEDIATE')
            try:
                user = candidate()  # Recheck under the write lock after the verified backup.
                if user is not None:
                    result['identity'] = identity(user)
                    result['path'] = create_invitation(owner, administrator=True, reset=True)
                    digest = hashlib.sha256(result['path'].partition('=')[2].encode()).hexdigest()
                    Invitation.objects.filter(token_hash=digest).update(expires_at=timezone.now()+timedelta(minutes=15))
                    AuditEvent.objects.create(actor=owner, action='account.desktop_first_password_invited')
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def verify():
        if result['identity'] is not None and identity(User.objects.get(username=owner)) != result['identity']:
            raise RuntimeError('Legacy owner identity changed during first-password setup')

    migrate_sqlite(connection.settings_dict['NAME'], state_root, issue, connections.close_all, verify)
    return result['path']
def first_owner_invitation():
    from django.db import connection
    from django.contrib.auth.models import User
    from server.research.views import create_invitation
    if connection.vendor != 'sqlite' or connection.in_atomic_block:
        raise RuntimeError('Desktop first-owner setup requires an independent SQLite transaction')
    with connection.cursor() as cursor:
        # Acquire the SQLite write lock before checking the empty account set.
        cursor.execute('BEGIN IMMEDIATE')
        try:
            path = None if User.objects.exists() else create_invitation('owner', administrator=True)
            connection.commit()
            return path
        except BaseException:
            connection.rollback()
            raise
