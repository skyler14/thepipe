local json = require("json")

User = {}
User.__index = User

function User:new(name, age)
    local instance = setmetatable({}, User)
    instance.name = name
    instance.age = age
    return instance
end

function User:greet()
    return "Hello, " .. self.name
end

function processData(data)
    local result = {}
    for i, v in ipairs(data) do
        if v > 0 then
            table.insert(result, v)
        end
    end
    return result
end

function main()
    local user = User:new("Alice", 30)
    print(user:greet())
end

main()
