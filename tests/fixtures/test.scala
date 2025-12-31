import scala.collection.mutable.ArrayBuffer

object Main {
  def main(args: Array[String]): Unit = {
    val user = new User("Alice", 30)
    println(user.greet())
  }
}

class User(val name: String, val age: Int) {
  def greet(): String = {
    s"Hello, $name"
  }
  
  def incrementAge(): Int = {
    age + 1
  }
}

trait Greeting {
  def sayHi(): String
}
