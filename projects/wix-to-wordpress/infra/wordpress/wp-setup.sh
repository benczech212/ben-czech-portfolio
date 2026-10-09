#!/usr/bin/env bash
# One-time WordPress install + baseline config. Run on the VM from /opt/wordpress (sudo), in an
# interactive ssh session: it prints the agents' application password once (see below).
set -euo pipefail
cd /opt/wordpress
wp() { docker compose run --rm -T cli wp "$@"; }

# Wait for MariaDB. The root password is read inside the db container from its own environment and
# handed to the client through MYSQL_PWD, so it never appears on a command line (ps, shell history).
until docker compose exec -T db sh -c 'MYSQL_PWD="${MARIADB_ROOT_PASSWORD}" exec mariadb-admin ping -uroot --silent'; do sleep 3; done

# The admin password goes to wp-cli on stdin (--prompt), not as a command-line argument.
if ! wp core is-installed 2>/dev/null; then
  docker compose run --rm -T cli sh -c 'printf "%s\n" "${WP_ADMIN_PASSWORD}" | wp core install --url="https://$SITE_HOST" \
    --title="${SITE_TITLE:-WordPress Staging}" --admin_user="$WP_ADMIN_USER" --admin_email="$WP_ADMIN_EMAIL" \
    --prompt=admin_password --skip-email'
fi

wp option update blog_public 0            # staging: discourage search engines
wp option update timezone_string America/New_York
wp rewrite structure '/%postname%/' --hard
wp plugin delete akismet hello 2>/dev/null || true
wp plugin install elementor --activate
wp theme install hello-elementor --activate
wp theme delete twentytwentythree twentytwentyfour 2>/dev/null || true

# Application password for agents (REST API / Elementor MCP). Printed once to this terminal and not
# written to disk on the VM. Copy it into your local .secrets/wordpress.env as WP_APP_PASSWORD.
# Lost it? Revoke and re-create:  wp user application-password delete <user> <uuid>, then re-run.
ADMIN_USER="$(grep '^WP_ADMIN_USER=' .env | cut -d= -f2-)"
if ! wp user application-password list "$ADMIN_USER" --field=name | grep -qx cs-agent; then
  echo "== Application password 'cs-agent' for $ADMIN_USER (shown once, not stored on the VM):"
  wp user application-password create "$ADMIN_USER" cs-agent --porcelain
else
  echo "== Application password 'cs-agent' already exists; not shown again."
fi

# Real cron instead of page-load wp-cron
echo '*/5 * * * * root curl -fsS -o /dev/null "https://'"$(grep '^SITE_HOST=' .env | cut -d= -f2-)"'/wp-cron.php?doing_wp_cron" ' > /etc/cron.d/wp-cron
echo done
