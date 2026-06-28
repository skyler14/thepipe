#include "tp_codegraph.h"

#include "cbm.h"
#include "foundation/compat.h"
#include "foundation/compat_thread.h"
#include "foundation/log.h"
#include "foundation/mem.h"
#include "foundation/platform.h"
#include "mcp/mcp.h"

#include <stdatomic.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

#ifndef TP_CODEGRAPH_VERSION
#define TP_CODEGRAPH_VERSION "dev"
#endif

#define TP_CODEGRAPH_ABI_VERSION "thepipe-codegraph/1"

struct tp_context {
    char *cache_dir;
    cbm_mcp_server_t *server;
    bool quiet;
};

static atomic_int g_init_state;
static cbm_mutex_t g_environment_lock;

static char *tp_strdup(const char *value) {
    size_t size = strlen(value) + 1;
    char *copy = malloc(size);
    if (copy) {
        memcpy(copy, value, size);
    }
    return copy;
}

static void tp_initialize(void) {
    int expected = 0;
    if (atomic_compare_exchange_strong(&g_init_state, &expected, 1)) {
        cbm_log_set_level(CBM_LOG_NONE);
        cbm_alloc_init();
        cbm_mem_init(0.5);
        cbm_mutex_init(&g_environment_lock);
        atomic_store(&g_init_state, 2);
        return;
    }
    while (atomic_load(&g_init_state) != 2) {
    }
}

tp_context *tp_context_new(const char *cache_dir) {
    if (!cache_dir || !cache_dir[0]) {
        return NULL;
    }
    tp_initialize();
    tp_context *context = calloc(1, sizeof(*context));
    if (!context) {
        return NULL;
    }
    context->cache_dir = tp_strdup(cache_dir);
    context->server = cbm_mcp_server_new(NULL);
    context->quiet = true;
    if (!context->cache_dir || !context->server) {
        tp_context_free(context);
        return NULL;
    }
    return context;
}

const char *tp_abi_version(void) {
    return TP_CODEGRAPH_ABI_VERSION;
}

int tp_context_set_quiet(tp_context *context, int quiet) {
    if (!context) {
        return 1;
    }
    context->quiet = quiet != 0;
    return 0;
}

int tp_context_call(tp_context *context, const char *tool,
                    const char *request_json, char **out_json) {
    if (!context || !context->server || !tool || !request_json || !out_json) {
        return 1;
    }
    *out_json = NULL;
    char previous[4096] = "";
    CBMLogLevel previous_log_level = cbm_log_get_level();

    cbm_mutex_lock(&g_environment_lock);
    if (context->quiet) {
        cbm_log_set_level(CBM_LOG_NONE);
    } else {
        cbm_log_set_level(CBM_LOG_INFO);
    }
    bool had_previous =
        cbm_safe_getenv("CBM_CACHE_DIR", previous, sizeof(previous), NULL) != NULL;
    int environment_status = cbm_setenv("CBM_CACHE_DIR", context->cache_dir, 1);
    if (environment_status == 0) {
        *out_json = cbm_mcp_handle_tool(context->server, tool, request_json);
    }
    if (had_previous) {
        (void)cbm_setenv("CBM_CACHE_DIR", previous, 1);
    } else {
        (void)cbm_unsetenv("CBM_CACHE_DIR");
    }
    cbm_log_set_level(previous_log_level);
    cbm_mutex_unlock(&g_environment_lock);

    if (environment_status != 0) {
        return 2;
    }
    return *out_json ? 0 : 3;
}

void tp_context_free(tp_context *context) {
    if (!context) {
        return;
    }
    cbm_mcp_server_free(context->server);
    free(context->cache_dir);
    free(context);
}

const char *tp_version(void) {
    return TP_CODEGRAPH_VERSION;
}

void tp_string_free(char *value) {
    free(value);
}
