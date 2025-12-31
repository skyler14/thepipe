using System;
using System.Collections.Generic;

namespace TestApp
{
    public class Program
    {
        private string name;
        
        public Program(string name)
        {
            this.name = name;
        }
        
        public void SayHello()
        {
            Console.WriteLine($"Hello, {name}");
        }
        
        static void Main(string[] args)
        {
            Program app = new Program("CSharp");
            app.SayHello();
        }
    }
    
    public class User
    {
        public string Username { get; set; }
        
        public User(string username)
        {
            Username = username;
        }
    }
}
