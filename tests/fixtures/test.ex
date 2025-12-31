defmodule User do
  defstruct name: "", age: 0
  
  def new(name, age) do
    %User{name: name, age: age}
  end
  
  def greet(%User{name: name}) do
    "Hello, #{name}"
  end
end

defmodule Main do
  def process_data(data) do
    data
    |> Enum.filter(&(&1 > 0))
    |> Enum.map(&(&1 * 2))
  end
  
  def run do
    user = User.new("Alice", 30)
    IO.puts(User.greet(user))
  end
end

Main.run()
