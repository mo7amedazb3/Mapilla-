DATABASE_PARAMETER = "factory_biometric_connection_alert.database"


def post_init_hook(env):
    # A database copy retains the original name and must not monitor stale
    # cloned device records until explicitly enabled in that database.
    env["ir.config_parameter"].sudo().set_param(DATABASE_PARAMETER, env.cr.dbname)


def uninstall_hook(env):
    env["ir.config_parameter"].sudo().search([
        ("key", "=", DATABASE_PARAMETER),
    ]).unlink()
