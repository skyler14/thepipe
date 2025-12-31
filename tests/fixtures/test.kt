import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        setupUI()
    }
    
    private fun setupUI() {
        // Setup UI
    }
    
    fun handleClick() {
        println("Clicked")
    }
}

data class User(val name: String, val age: Int) {
    fun greet(): String {
        return "Hello, $name"
    }
}

object Constants {
    const val APP_NAME = "Test"
}

fun main() {
    println("Kotlin test")
}
