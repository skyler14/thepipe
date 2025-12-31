require 'json'
require 'net/http'

class User
  attr_accessor :name, :age
  
  def initialize(name, age)
    @name = name
    @age = age
  end
  
  def greet
    "Hello, #{@name}"
  end
end

module Utils
  def self.format_date(date)
    date.strftime("%Y-%m-%d")
  end
end

def main
  user = User.new("Alice", 30)
  puts user.greet
end

main if __FILE__ == $0
