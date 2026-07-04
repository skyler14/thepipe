#include "tp_codegraph.h"

#include "cbm.h"
#include "foundation/compat.h"
#include "foundation/compat_thread.h"
#include "foundation/log.h"
#include "foundation/mem.h"
#include "foundation/platform.h"
#include "mcp/mcp.h"
#include "store/store.h"

#include <stdatomic.h>
#include <stdbool.h>
#include <stdio.h>
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

struct tp_store {
    char *db_path;
    char *cache_dir;
    char *project;
    cbm_mcp_server_t *server;
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

static char *tp_dirname_dup(const char *path) {
    const char *last = strrchr(path, '/');
#if defined(_WIN32)
    const char *backslash = strrchr(path, '\\');
    if (!last || (backslash && backslash > last)) {
        last = backslash;
    }
#endif
    if (!last || last == path) {
        return tp_strdup(last == path ? "/" : ".");
    }
    size_t size = (size_t)(last - path);
    char *copy = malloc(size + 1);
    if (!copy) {
        return NULL;
    }
    memcpy(copy, path, size);
    copy[size] = '\0';
    return copy;
}

static char *tp_json_escape(const char *value) {
    size_t extra = 0;
    for (const char *p = value; *p; p++) {
        if (*p == '"' || *p == '\\') {
            extra++;
        }
    }
    size_t input_size = strlen(value);
    char *copy = malloc(input_size + extra + 1);
    if (!copy) {
        return NULL;
    }
    char *out = copy;
    for (const char *p = value; *p; p++) {
        if (*p == '"' || *p == '\\') {
            *out++ = '\\';
        }
        *out++ = *p;
    }
    *out = '\0';
    return copy;
}

static bool tp_json_has_key(const char *json, const char *key) {
    char needle[256];
    if (strlen(key) + 3 >= sizeof(needle)) {
        return false;
    }
    snprintf(needle, sizeof(needle), "\"%s\"", key);
    return strstr(json, needle) != NULL;
}

static char *tp_payload_with_project(const char *request_json, const char *project) {
    if (tp_json_has_key(request_json, "project") ||
        tp_json_has_key(request_json, "project_name") ||
        tp_json_has_key(request_json, "project_id") ||
        tp_json_has_key(request_json, "projectName")) {
        return tp_strdup(request_json);
    }
    const char *end = strrchr(request_json, '}');
    if (!end) {
        return NULL;
    }
    char *escaped = tp_json_escape(project);
    if (!escaped) {
        return NULL;
    }
    const char *cursor = request_json;
    while (*cursor == ' ' || *cursor == '\t' || *cursor == '\r' || *cursor == '\n') {
        cursor++;
    }
    bool empty_object = cursor[0] == '{' && cursor + 1 == end;
    size_t prefix = (size_t)(end - request_json);
    size_t needed = prefix + strlen(escaped) + 32;
    char *merged = malloc(needed);
    if (!merged) {
        free(escaped);
        return NULL;
    }
    snprintf(merged, needed, "%.*s%s\"project\":\"%s\"%s", (int)prefix, request_json,
             empty_object ? "" : ",", escaped, end);
    free(escaped);
    return merged;
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

static int tp_dispatch_with_cache(cbm_mcp_server_t *server, const char *cache_dir, const char *tool,
                                  const char *request_json, char **out_json) {
    if (!server || !cache_dir || !tool || !request_json || !out_json) {
        return 1;
    }
    *out_json = NULL;
    char previous[4096] = "";
    CBMLogLevel previous_log_level = cbm_log_get_level();

    cbm_mutex_lock(&g_environment_lock);
    cbm_log_set_level(CBM_LOG_NONE);
    bool had_previous =
        cbm_safe_getenv("CBM_CACHE_DIR", previous, sizeof(previous), NULL) != NULL;
    int environment_status = cbm_setenv("CBM_CACHE_DIR", cache_dir, 1);
    if (environment_status == 0) {
        *out_json = cbm_mcp_handle_tool(server, tool, request_json);
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

    if (!context->quiet) {
        CBMLogLevel previous_log_level = cbm_log_get_level();
        cbm_log_set_level(CBM_LOG_INFO);
        int status =
            tp_dispatch_with_cache(context->server, context->cache_dir, tool, request_json, out_json);
        cbm_log_set_level(previous_log_level);
        return status;
    }
    return tp_dispatch_with_cache(context->server, context->cache_dir, tool, request_json, out_json);
}

void tp_context_free(tp_context *context) {
    if (!context) {
        return;
    }
    cbm_mcp_server_free(context->server);
    free(context->cache_dir);
    free(context);
}

tp_store *tp_store_open_query(const char *db_path) {
    if (!db_path || !db_path[0]) {
        return NULL;
    }
    tp_initialize();
    cbm_store_t *raw = cbm_store_open_path_query(db_path);
    if (!raw) {
        return NULL;
    }

    cbm_project_t *projects = NULL;
    int count = 0;
    if (cbm_store_list_projects(raw, &projects, &count) != CBM_STORE_OK || count != 1 ||
        !projects[0].name || !projects[0].name[0]) {
        cbm_store_free_projects(projects, count);
        cbm_store_close(raw);
        return NULL;
    }

    tp_store *store = calloc(1, sizeof(*store));
    if (store) {
        store->db_path = tp_strdup(db_path);
        store->cache_dir = tp_dirname_dup(db_path);
        store->project = tp_strdup(projects[0].name);
        store->server = cbm_mcp_server_new(NULL);
        if (!store->db_path || !store->cache_dir || !store->project || !store->server) {
            tp_store_close(store);
            store = NULL;
        }
    }

    cbm_store_free_projects(projects, count);
    cbm_store_close(raw);
    return store;
}

int tp_store_call(tp_store *store, const char *action, const char *request_json, char **out_json) {
    if (!store || !store->server || !store->cache_dir || !store->project || !action ||
        !request_json || !out_json) {
        return 1;
    }
    char *payload = tp_payload_with_project(request_json, store->project);
    if (!payload) {
        return 4;
    }
    int status = tp_dispatch_with_cache(store->server, store->cache_dir, action, payload, out_json);
    free(payload);
    return status;
}

int tp_cypher_query(tp_store *store, const char *request_json, char **out_json) {
    return tp_store_call(store, "query_graph", request_json, out_json);
}

void tp_store_close(tp_store *store) {
    if (!store) {
        return;
    }
    cbm_mcp_server_free(store->server);
    free(store->db_path);
    free(store->cache_dir);
    free(store->project);
    free(store);
}

const char *tp_version(void) {
    return TP_CODEGRAPH_VERSION;
}

void tp_string_free(char *value) {
    free(value);
}
